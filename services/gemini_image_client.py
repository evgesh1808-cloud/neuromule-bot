"""Генерация изображений через Gemini API (Imagen 4, Nano Banana) — httpx + GEMINI_API_KEY[/_2]."""

from __future__ import annotations

import base64
import itertools
import logging
import threading
from dataclasses import dataclass

import httpx

from config import settings
from services.hd_logic import _configure_genai

logger = logging.getLogger(__name__)

_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
# Лимит сырых байт референса (до base64): защита от 400 / «message too long».
_MAX_REFERENCE_IMAGE_BYTES = 4 * 1024 * 1024

_KEY_CYCLE = None
_KEY_LOCK = threading.Lock()
_LAST_KEY = ""


@dataclass(frozen=True)
class GeminiImageResult:
    """URL (если провайдер отдал ссылку) или сырые байты изображения."""

    url: str | None = None
    data: bytes | None = None

    def has_image(self) -> bool:
        return bool(self.url or self.data)


def collect_gemini_api_keys() -> list[str]:
    """Пул: GEMINI_API_KEY, затем GEMINI_API_KEY_2 (без дублей)."""
    keys: list[str] = []
    for raw in (
        getattr(settings, "gemini_api_key", None),
        getattr(settings, "gemini_api_key_2", None),
    ):
        k = (raw or "").strip()
        if k and k not in keys:
            keys.append(k)
    return keys


def reset_gemini_key_rotator_for_tests() -> None:
    global _KEY_CYCLE, _LAST_KEY
    with _KEY_LOCK:
        _KEY_CYCLE = None
        _LAST_KEY = ""


def _next_gemini_key() -> str:
    """Round-robin по пулу ключей."""
    global _KEY_CYCLE, _LAST_KEY
    keys = collect_gemini_api_keys()
    if not keys:
        raise RuntimeError("Задайте GEMINI_API_KEY (и опционально GEMINI_API_KEY_2) в .env.")
    with _KEY_LOCK:
        if _KEY_CYCLE is None:
            _KEY_CYCLE = itertools.cycle(keys)
            if len(keys) > 1:
                logger.info("Gemini key pool: %s keys (round-robin)", len(keys))
        _LAST_KEY = next(_KEY_CYCLE)
        return _LAST_KEY


def _api_key(*, prefer: str | None = None) -> str:
    _configure_genai()
    if prefer and prefer.strip():
        return prefer.strip()
    return _next_gemini_key()


def _coerce_prompt_text(prompt: object) -> str:
    """Промпт — только текст. Байты/BufferedInputFile в строку не склеиваем."""
    if isinstance(prompt, (bytes, bytearray, memoryview)):
        raise RuntimeError(
            "Gemini prompt must be str, got binary image bytes "
            "(pass image via reference_image_bytes / inline_data)"
        )
    text = str(prompt or "").strip()
    if not text:
        raise RuntimeError("Gemini prompt is empty")
    return text


def _normalize_reference_mime(mime: str | None) -> str:
    raw = (mime or "image/jpeg").strip().lower() or "image/jpeg"
    if raw in ("image/jpg", "jpg", "jpeg"):
        return "image/jpeg"
    if raw in ("png", "image/png"):
        return "image/png"
    if raw in ("webp", "image/webp"):
        return "image/webp"
    if raw.startswith("image/"):
        return raw
    return "image/jpeg"


def _encode_reference_image_b64(reference_image_bytes: object) -> str:
    if isinstance(reference_image_bytes, memoryview):
        raw = reference_image_bytes.tobytes()
    elif isinstance(reference_image_bytes, (bytes, bytearray)):
        raw = bytes(reference_image_bytes)
    else:
        raise RuntimeError(
            "reference_image_bytes must be bytes, got "
            f"{type(reference_image_bytes).__name__}"
        )
    if not raw:
        raise RuntimeError("reference_image_bytes is empty")
    if len(raw) > _MAX_REFERENCE_IMAGE_BYTES:
        raise RuntimeError(
            f"reference image too large ({len(raw)} bytes); "
            f"max {_MAX_REFERENCE_IMAGE_BYTES}"
        )
    return base64.b64encode(raw).decode("ascii")


def build_gemini_generate_content_body(
    prompt: object,
    *,
    reference_image_bytes: bytes | None = None,
    reference_mime: str = "image/jpeg",
) -> dict:
    """Официальный multimodal payload для ``generateContent``.

    Текст — ``{"text": ...}``; картинка — ``inline_data.mime_type`` + base64 ``data``.
    Сырые байты в text-поле никогда не попадают.
    """
    text = _coerce_prompt_text(prompt)
    parts: list[dict] = [{"text": text}]
    if reference_image_bytes is not None:
        parts.append(
            {
                "inline_data": {
                    "mime_type": _normalize_reference_mime(reference_mime),
                    "data": _encode_reference_image_b64(reference_image_bytes),
                }
            }
        )
    return {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }


def _extract_inline_image_bytes(payload: dict) -> bytes | None:
    for cand in payload.get("candidates") or []:
        content = cand.get("content") or {}
        for part in content.get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            raw = inline.get("data")
            if raw:
                return base64.b64decode(raw)
    return None


def _extract_generate_images_bytes(payload: dict) -> bytes | None:
    for item in payload.get("generatedImages") or payload.get("generated_images") or []:
        image = item.get("image") or {}
        raw = image.get("imageBytes") or image.get("image_bytes")
        if raw:
            return base64.b64decode(raw)
        uri = image.get("uri") or image.get("gcsUri") or image.get("gcs_uri")
        if uri and str(uri).startswith(("http://", "https://")):
            return None  # caller handles URL via separate field
    return None


def _extract_generate_images_url(payload: dict) -> str | None:
    for item in payload.get("generatedImages") or payload.get("generated_images") or []:
        image = item.get("image") or {}
        uri = image.get("uri") or image.get("gcsUri") or image.get("gcs_uri")
        if uri and str(uri).startswith(("http://", "https://")):
            return str(uri)
    return None


async def _post_json(
    path: str,
    body: dict,
    *,
    timeout: float = 120.0,
    api_key: str | None = None,
) -> dict:
    key = _api_key(prefer=api_key)
    url = f"{_GEMINI_API_BASE}/{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, params={"key": key}, json=body)
            if resp.status_code != 200:
                logger.error("Gemini image API %s: %s", resp.status_code, resp.text[:800])
                resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException as exc:
        logger.error("Gemini image API timeout path=%s", path)
        raise TimeoutError("Gemini image API timeout") from exc
    except httpx.HTTPError as exc:
        logger.error("Gemini image API HTTP error path=%s: %s", path, exc)
        raise RuntimeError(f"Gemini image API error: {exc}") from exc


async def _post_json_with_key_failover(path: str, body: dict, *, timeout: float = 120.0) -> dict:
    """Пробует все ключи пула; при 429/403 — следующий ключ."""
    keys = collect_gemini_api_keys()
    if not keys:
        raise RuntimeError("Задайте GEMINI_API_KEY (и опционально GEMINI_API_KEY_2) в .env.")

    last_exc: BaseException | None = None
    # Стартуем с RR-ключа, затем остальные.
    start = _next_gemini_key()
    ordered = [start] + [k for k in keys if k != start]

    for i, key in enumerate(ordered):
        try:
            return await _post_json(path, body, timeout=timeout, api_key=key)
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            code = exc.response.status_code if exc.response is not None else 0
            if code in (429, 403) and i + 1 < len(ordered):
                logger.warning(
                    "Gemini key ...%s HTTP %s — failover to next key",
                    key[-6:],
                    code,
                )
                continue
            raise RuntimeError(f"Gemini image API error: {exc}") from exc
        except RuntimeError as exc:
            last_exc = exc
            msg = str(exc).lower()
            if ("429" in msg or "403" in msg or "resource_exhausted" in msg) and i + 1 < len(
                ordered
            ):
                logger.warning("Gemini key failover after: %s", exc)
                continue
            raise
    if last_exc:
        raise RuntimeError(f"Gemini image API error: {last_exc}") from last_exc
    raise RuntimeError("Gemini image API: no keys")


async def generate_imagen_fast(prompt: str) -> GeminiImageResult:
    """Imagen 4 Fast (бесплатный контур Imagen 4 в AI Studio)."""
    return await generate_imagen_model(prompt, "imagen-4.0-fast-generate-001")


async def generate_imagen_model(
    prompt: str,
    model: str,
    *,
    api_key: str | None = None,
) -> GeminiImageResult:
    """Text-to-image через Imagen ``:generateImages`` (без reference image)."""
    mid = (model or "").strip() or "imagen-3.0-generate-002"
    body = {
        "prompt": _coerce_prompt_text(prompt),
        "config": {"numberOfImages": 1},
    }
    path = f"models/{mid}:generateImages"
    if api_key and api_key.strip():
        payload = await _post_json(path, body, api_key=api_key.strip())
    else:
        payload = await _post_json_with_key_failover(path, body)
    url = _extract_generate_images_url(payload)
    if url:
        return GeminiImageResult(url=url)
    data = _extract_generate_images_bytes(payload)
    if data:
        return GeminiImageResult(data=data)
    raise RuntimeError(f"Imagen model {mid} returned no image")


async def generate_gemini_image_model(prompt: str, model: str) -> GeminiImageResult:
    """Gemini image-preview модели (Nano Banana 2 / Pro) или Imagen text-to-image."""
    mid = (model or "").strip().lower()
    if mid.startswith("imagen-") or mid.startswith("imagen."):
        return await generate_imagen_model(prompt, model)
    return await generate_gemini_image_with_reference(prompt, model)


async def generate_gemini_image_with_reference(
    prompt: str,
    model: str,
    *,
    reference_image_bytes: bytes | None = None,
    reference_mime: str = "image/jpeg",
    api_key: str | None = None,
) -> GeminiImageResult:
    """Text-to-image или image-to-image через Gemini ``generateContent``."""
    body = build_gemini_generate_content_body(
        prompt,
        reference_image_bytes=reference_image_bytes,
        reference_mime=reference_mime,
    )
    # Защита: в text-части не должно оказаться бинарного мусора.
    text_part = body["contents"][0]["parts"][0].get("text") or ""
    if len(text_part) > 8_000:
        raise RuntimeError(
            f"Gemini text prompt suspiciously long ({len(text_part)} chars); "
            "refusing possible binary/text mix"
        )
    path = f"models/{model}:generateContent"
    if api_key and api_key.strip():
        payload = await _post_json(path, body, api_key=api_key.strip())
    else:
        payload = await _post_json_with_key_failover(path, body)
    data = _extract_inline_image_bytes(payload)
    if data:
        return GeminiImageResult(data=data)
    raise RuntimeError(f"Gemini model {model} returned no image")
