"""Генерация изображений через Gemini API (Imagen 4, Nano Banana) — httpx + GEMINI_API_KEY."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

import httpx

from config import settings
from services.hd_logic import _configure_genai

logger = logging.getLogger(__name__)

_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


@dataclass(frozen=True)
class GeminiImageResult:
    """URL (если провайдер отдал ссылку) или сырые байты изображения."""

    url: str | None = None
    data: bytes | None = None

    def has_image(self) -> bool:
        return bool(self.url or self.data)


def _api_key() -> str:
    _configure_genai()
    key = (settings.gemini_api_key or "").strip()
    if not key:
        raise RuntimeError("Задайте GEMINI_API_KEY в .env.")
    return key


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


async def _post_json(path: str, body: dict, *, timeout: float = 120.0) -> dict:
    key = _api_key()
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


async def generate_imagen_fast(prompt: str) -> GeminiImageResult:
    """Imagen 4 Fast (бесплатный контур Imagen 4 в AI Studio)."""
    model = "imagen-4.0-fast-generate-001"
    payload = await _post_json(
        f"models/{model}:generateImages",
        {"prompt": prompt, "config": {"numberOfImages": 1}},
    )
    url = _extract_generate_images_url(payload)
    if url:
        return GeminiImageResult(url=url)
    data = _extract_generate_images_bytes(payload)
    if data:
        return GeminiImageResult(data=data)
    raise RuntimeError("Imagen API returned no image")


async def generate_gemini_image_model(prompt: str, model: str) -> GeminiImageResult:
    """Gemini image-preview модели (Nano Banana 2 / Pro)."""
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    payload = await _post_json(f"models/{model}:generateContent", body)
    data = _extract_inline_image_bytes(payload)
    if data:
        return GeminiImageResult(data=data)
    raise RuntimeError(f"Gemini model {model} returned no image")
