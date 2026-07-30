"""FREE Nano Banana: строгий Round-Robin по 4 API-ключам.

Порядок слотов (непустые только):
  1. GEMINI_API_KEY
  2. GEMINI_API_KEY_2
  3. OPENROUTER_API_KEY  (модель …:free, allow_fallbacks=false)
  4. OPENROUTER_API_KEY_2

``Semaphore(1)`` + ``asyncio.sleep(2)`` → ~8с между повторными вызовами одного ключа
при полном пуле из 4.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import Any, Literal, TypedDict

import httpx

from config import settings
from services import metrics
from services.api_resilience import ExternalApiError
from services.gemini_image_client import GeminiImageResult, generate_gemini_image_with_reference

logger = logging.getLogger(__name__)

_FREE_IMAGE_SEM: asyncio.Semaphore | None = None
_B64_URL_RE = re.compile(r"data:image/[^;]+;base64,([A-Za-z0-9+/=]+)")

DEFAULT_OPENROUTER_NANO_BANANA = "google/gemini-2.5-flash-image-preview:free"
DEFAULT_GEMINI_NANO_BANANA = "gemini-2.5-flash-image-preview"

# До 1 основной + 3 смещения внутри пула при 429/403/402.
_FAILOVER_SHIFTS = 3
_POST_REQUEST_PAUSE_SEC = 2.0

ProviderType = Literal["gemini", "openrouter"]


class ProviderSlot(TypedDict):
    type: ProviderType
    key: str


class FreeImageCascadeExhausted(ExternalApiError):
    """Все слоты пула недоступны или исчерпали лимит."""


class OpenRouterPaidBlockedError(ExternalApiError):
    """Попытка уйти на платную модель OpenRouter — запрещено для FREE."""


# ─── Глобальный индекс Round-Robin (in-process) ─────────────────────────────

global_provider_index: int = 0
_provider_index_lock = asyncio.Lock()


def build_free_image_providers() -> list[ProviderSlot]:
    """Упорядоченный пул из 4 ключей; пустые отфильтрованы."""
    raw: list[tuple[ProviderType, str]] = [
        ("gemini", (getattr(settings, "gemini_api_key", None) or "").strip()),
        ("gemini", (getattr(settings, "gemini_api_key_2", None) or "").strip()),
        ("openrouter", (getattr(settings, "openrouter_key", None) or "").strip()),
        ("openrouter", (getattr(settings, "openrouter_key_2", None) or "").strip()),
    ]
    return [{"type": t, "key": k} for t, k in raw if k]


async def _peek_provider_index(n: int) -> int:
    """Текущий слот: ``providers[global_provider_index % n]`` (без сдвига)."""
    async with _provider_index_lock:
        return global_provider_index % max(1, n)


async def _advance_provider_index() -> None:
    """+1 после каждого обращения к API (успех или ошибка)."""
    global global_provider_index
    async with _provider_index_lock:
        global_provider_index += 1


def reset_free_image_rr_for_tests() -> None:
    """Только тесты: обнулить индекс и семафор."""
    global global_provider_index, _FREE_IMAGE_SEM
    global_provider_index = 0
    _FREE_IMAGE_SEM = None


def _semaphore() -> asyncio.Semaphore:
    global _FREE_IMAGE_SEM
    if _FREE_IMAGE_SEM is None:
        # Строго 1: запросы идут по одному, RR по ключам.
        limit = max(1, int(getattr(settings, "free_image_semaphore_limit", 1) or 1))
        _FREE_IMAGE_SEM = asyncio.Semaphore(limit)
    return _FREE_IMAGE_SEM


def reset_free_image_semaphore_for_tests() -> None:
    reset_free_image_rr_for_tests()


def ensure_openrouter_free_model(model: str) -> str:
    """Жёстко требует суффикс ``:free``."""
    raw = (model or "").strip()
    if not raw:
        return DEFAULT_OPENROUTER_NANO_BANANA
    base = raw.split(":", 1)[0].strip()
    if not base:
        return DEFAULT_OPENROUTER_NANO_BANANA
    if ":" in raw and not raw.endswith(":free"):
        raise OpenRouterPaidBlockedError(
            "OpenRouter",
            f"paid/non-free model blocked for FREE Nano Banana: {raw}",
        )
    if not raw.endswith(":free"):
        return f"{base}:free"
    return raw


def _is_rate_limit_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        x in msg
        for x in (
            "http 429",
            "http 403",
            "http 402",
            "429",
            "403",
            "402",
            "resource_exhausted",
            "rate limit",
            "quota",
        )
    )


def _extract_b64_from_payload(payload: dict[str, Any]) -> bytes | None:
    choices = payload.get("choices") or []
    if choices:
        msg = (choices[0].get("message") or {}) if isinstance(choices[0], dict) else {}
        for img in msg.get("images") or []:
            if not isinstance(img, dict):
                continue
            url = ((img.get("image_url") or {}).get("url") or img.get("url") or "")
            if isinstance(url, str) and url.startswith("data:"):
                m = _B64_URL_RE.search(url)
                if m:
                    return base64.b64decode(m.group(1))
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "image_url":
                    url = (part.get("image_url") or {}).get("url") or ""
                    if isinstance(url, str) and "base64," in url:
                        m = _B64_URL_RE.search(url)
                        if m:
                            return base64.b64decode(m.group(1))
                inline = part.get("inline_data") or part.get("inlineData") or {}
                raw = inline.get("data")
                if raw:
                    return base64.b64decode(raw)
        if isinstance(content, str):
            m = _B64_URL_RE.search(content)
            if m:
                return base64.b64decode(m.group(1))
    data_list = payload.get("data") or []
    for item in data_list:
        if not isinstance(item, dict):
            continue
        b64 = item.get("b64_json") or item.get("b64")
        if b64:
            return base64.b64decode(b64)
    return None


def _build_user_content(
    prompt: str,
    *,
    reference_image_bytes: bytes | None,
    reference_mime: str,
) -> list[dict[str, Any]] | str:
    """OpenRouter chat content: текст отдельно, картинка — data-URL (base64)."""
    if isinstance(prompt, (bytes, bytearray, memoryview)):
        raise ExternalApiError(
            "NanoBanana",
            "prompt must be str, got binary (use reference_image_bytes)",
        )
    text = (prompt or "").strip()
    if not text:
        raise ExternalApiError("NanoBanana", "пустой промпт")
    if len(text) > 8_000:
        raise ExternalApiError(
            "NanoBanana",
            f"prompt suspiciously long ({len(text)} chars); refusing binary/text mix",
        )
    if not reference_image_bytes:
        return text

    if isinstance(reference_image_bytes, memoryview):
        raw = reference_image_bytes.tobytes()
    elif isinstance(reference_image_bytes, (bytes, bytearray)):
        raw = bytes(reference_image_bytes)
    else:
        raise ExternalApiError(
            "NanoBanana",
            f"reference_image_bytes must be bytes, got {type(reference_image_bytes).__name__}",
        )
    if not raw:
        raise ExternalApiError("NanoBanana", "пустой reference image")
    # ~4MB raw → ~5.3MB base64; выше — типичный 400 у провайдера.
    if len(raw) > 4 * 1024 * 1024:
        raise ExternalApiError(
            "NanoBanana",
            f"reference image too large ({len(raw)} bytes)",
        )

    b64 = base64.b64encode(raw).decode("ascii")
    mime = (reference_mime or "image/jpeg").strip() or "image/jpeg"
    if mime in ("jpg", "jpeg", "image/jpg"):
        mime = "image/jpeg"
    elif mime == "png":
        mime = "image/png"
    return [
        {"type": "text", "text": text},
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        },
    ]


async def _call_openrouter(
    prompt: str,
    *,
    api_key: str,
    reference_image_bytes: bytes | None,
    reference_mime: str,
    timeout: float,
) -> GeminiImageResult:
    provider = "OpenRouter"
    model = ensure_openrouter_free_model(
        settings.free_image_openrouter_model or DEFAULT_OPENROUTER_NANO_BANANA
    )
    content = _build_user_content(
        prompt,
        reference_image_bytes=reference_image_bytes,
        reference_mime=reference_mime,
    )
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "modalities": ["image", "text"],
        "provider": {
            "allow_fallbacks": False,
            "require_parameters": True,
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://neuromule.bot",
        "X-Title": "NeuroMule Nano Banana FREE",
    }
    url = (settings.openrouter_chat_url or "https://openrouter.ai/api/v1/chat/completions").strip()

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
            resp = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException as exc:
        raise ExternalApiError(provider, "timeout") from exc
    except httpx.HTTPError as exc:
        raise ExternalApiError(provider, str(exc)) from exc

    if resp.status_code != 200:
        raise ExternalApiError(provider, f"HTTP {resp.status_code}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise ExternalApiError(provider, "invalid JSON") from exc

    used = str(payload.get("model") or "").strip()
    if used and ":free" not in used.lower():
        raise OpenRouterPaidBlockedError(
            provider,
            f"response model is not :free ({used}); refusing paid billing",
        )

    data = _extract_b64_from_payload(payload)
    if data:
        metrics.incr("free_image.cascade.ok", labels={"provider": provider})
        return GeminiImageResult(data=data)
    raise ExternalApiError(provider, "no image in response")


async def _call_gemini(
    prompt: str,
    *,
    api_key: str,
    reference_image_bytes: bytes | None,
    reference_mime: str,
    timeout: float,
) -> GeminiImageResult:
    provider = "Gemini"
    model = (
        settings.free_image_gemini_model or DEFAULT_GEMINI_NANO_BANANA
    ).strip() or DEFAULT_GEMINI_NANO_BANANA
    try:
        async with asyncio.timeout(timeout):
            result = await generate_gemini_image_with_reference(
                prompt,
                model,
                reference_image_bytes=reference_image_bytes,
                reference_mime=reference_mime,
                api_key=api_key,
            )
    except TimeoutError as exc:
        raise ExternalApiError(provider, "timeout") from exc
    except RuntimeError as exc:
        raise ExternalApiError(provider, str(exc)) from exc

    if result.has_image():
        metrics.incr("free_image.cascade.ok", labels={"provider": provider})
        return result
    raise ExternalApiError(provider, "no image in response")


async def _invoke_slot(
    slot: ProviderSlot,
    prompt: str,
    *,
    reference_image_bytes: bytes | None,
    reference_mime: str,
    timeout: float,
) -> GeminiImageResult:
    if slot["type"] == "gemini":
        return await _call_gemini(
            prompt,
            api_key=slot["key"],
            reference_image_bytes=reference_image_bytes,
            reference_mime=reference_mime,
            timeout=timeout,
        )
    return await _call_openrouter(
        prompt,
        api_key=slot["key"],
        reference_image_bytes=reference_image_bytes,
        reference_mime=reference_mime,
        timeout=timeout,
    )


async def generate_free_tier_image(
    prompt: str,
    *,
    reference_image_bytes: bytes | None = None,
    reference_mime: str = "image/jpeg",
) -> GeminiImageResult:
    """
    Nano Banana FREE: строгий RR по пулу ключей + Semaphore(1) + pause 2s.

    Raises:
        FreeImageCascadeExhausted: пул пуст или все попытки (1+3) провалились.
    """
    providers = build_free_image_providers()
    if not providers:
        raise FreeImageCascadeExhausted(
            "NanoBananaCascade",
            "no API keys: set GEMINI_API_KEY[_2] and/or OPENROUTER_API_KEY[_2]",
        )

    timeout = float(settings.free_image_cascade_timeout_sec or 120.0)
    n = len(providers)
    max_attempts = min(n, 1 + _FAILOVER_SHIFTS)
    last_err = "unknown"

    async with _semaphore():
        try:
            for shift in range(max_attempts):
                idx = await _peek_provider_index(n)
                slot = providers[idx]
                label = f"{slot['type']}:...{slot['key'][-6:]}"
                try:
                    logger.info(
                        "Nano Banana RR idx=%s shift=%s slot=%s pool=%s",
                        idx,
                        shift,
                        label,
                        n,
                    )
                    result = await _invoke_slot(
                        slot,
                        prompt,
                        reference_image_bytes=reference_image_bytes,
                        reference_mime=reference_mime,
                        timeout=timeout,
                    )
                    metrics.incr(
                        "free_image.rr",
                        labels={"type": slot["type"], "shift": str(shift)},
                    )
                    return result
                except ExternalApiError as exc:
                    last_err = str(exc)
                    metrics.incr(
                        "free_image.cascade.fail",
                        labels={"provider": slot["type"], "reason": "error"},
                    )
                    if isinstance(exc, OpenRouterPaidBlockedError):
                        break
                    # Failover только на 429/403/402 — до 3 смещений внутри пула.
                    if _is_rate_limit_error(exc) and shift + 1 < max_attempts:
                        logger.warning(
                            "Nano Banana failover %s → next (%s)",
                            label,
                            exc,
                        )
                        continue
                    break
                finally:
                    await _advance_provider_index()
        finally:
            # Безопасная пауза: при 4 ключах → ~8с между вызовами одного ключа.
            pause = float(
                getattr(settings, "free_image_key_pause_sec", _POST_REQUEST_PAUSE_SEC)
                or _POST_REQUEST_PAUSE_SEC
            )
            await asyncio.sleep(max(0.0, pause))

    metrics.incr("free_image.cascade.exhausted")
    logger.error(
        "Каскад бесплатной генерации полностью истощен. Причина: %s",
        last_err,
    )
    raise FreeImageCascadeExhausted("NanoBananaCascade", last_err)
