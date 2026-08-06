"""Pollinations.ai — Flux для FREE-tier фото.

Актуально (2026): бесплатный legacy ``image.pollinations.ai`` часто отвечает
HTTP 500/402 (Insufficient balance / pollen). Для стабильной работы нужен
``POLLINATIONS_API_KEY`` (https://enter.pollinations.ai/keys) с балансом pollen.
С ключом — ``gen.pollinations.ai`` + Bearer; без — legacy GET (может быть недоступен).
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import httpx

from config import settings
from services import metrics
from services.api_resilience import ExternalApiError
from services.gemini_image_client import GeminiImageResult
from services.streaming_download import DEFAULT_CHUNK_SIZE, DEFAULT_MAX_BYTES

logger = logging.getLogger(__name__)

# Legacy: без ключа; на практике часто 402/500 без pollen-баланса.
POLLINATIONS_IMAGE_BASE_LEGACY = "https://image.pollinations.ai/prompt"
# Новый API: нужен sk_/pk_ с enter.pollinations.ai.
POLLINATIONS_IMAGE_BASE_GEN = "https://gen.pollinations.ai/image"
POLLINATIONS_FLUX_MODEL = "flux"
DEFAULT_IMAGE_SIZE = 1024
MAX_PROMPT_CHARS = 1500
POLLINATIONS_TIMEOUT_SEC = 90.0
POLLINATIONS_MAX_ATTEMPTS = 2
POLLINATIONS_RETRY_DELAY_SEC = 2.0

_IMAGE_MAGIC_PREFIXES = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG\r\n\x1a\n",
    b"RIFF",  # WebP (RIFF....WEBP)
)


def _is_valid_image_bytes(data: bytes) -> bool:
    if len(data) < 12:
        return False
    if data.startswith(_IMAGE_MAGIC_PREFIXES[:2]):
        return True
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    return False


def _pollinations_error_retryable(exc: ExternalApiError) -> bool:
    msg = str(exc).lower()
    return any(
        x in msg
        for x in (
            "timeout",
            "http 5",
            "http 429",
            "http 502",
            "http 503",
            "http 504",
            "empty",
            "invalid image",
            "response too large",
        )
    )


def _pollinations_api_key() -> str:
    return (getattr(settings, "pollinations_api_key", None) or "").strip()


def build_pollinations_flux_url(
    prompt: str,
    *,
    width: int = DEFAULT_IMAGE_SIZE,
    height: int = DEFAULT_IMAGE_SIZE,
    api_key: str | None = None,
) -> str:
    """Собирает URL генерации Flux (legacy без ключа / gen с ключом)."""
    text = (prompt or "").strip()[:MAX_PROMPT_CHARS]
    if not text:
        raise ExternalApiError("Pollinations", "пустой промпт")
    key = (api_key if api_key is not None else _pollinations_api_key()).strip()
    encoded = quote(text, safe="")
    base = POLLINATIONS_IMAGE_BASE_GEN if key else POLLINATIONS_IMAGE_BASE_LEGACY
    url = (
        f"{base}/{encoded}"
        f"?model={POLLINATIONS_FLUX_MODEL}"
        f"&width={int(width)}&height={int(height)}"
        f"&nologo=true&enhance=false"
    )
    if key:
        # Дублируем в query — часть клиентов Pollinations читает только ?key=.
        url = f"{url}&key={quote(key, safe='')}"
    return url


async def generate_flux_schnell_image(prompt: str) -> GeminiImageResult:
    """
    Генерирует изображение Flux через Pollinations.

    Возвращает ``GeminiImageResult(data=...)`` — байты для ``send_photo``.
    """
    last_exc: ExternalApiError | None = None
    for attempt in range(1, POLLINATIONS_MAX_ATTEMPTS + 1):
        try:
            return await _generate_flux_schnell_image_once(prompt)
        except ExternalApiError as exc:
            last_exc = exc
            if attempt < POLLINATIONS_MAX_ATTEMPTS and _pollinations_error_retryable(exc):
                logger.warning(
                    "pollinations flux retry %s/%s: %s",
                    attempt,
                    POLLINATIONS_MAX_ATTEMPTS,
                    exc,
                )
                await asyncio.sleep(POLLINATIONS_RETRY_DELAY_SEC)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise ExternalApiError("Pollinations", "Flux: empty result")


async def _generate_flux_schnell_image_once(prompt: str) -> GeminiImageResult:
    key = _pollinations_api_key()
    url = build_pollinations_flux_url(prompt, api_key=key or None)
    headers: dict[str, str] = {
        # Некоторые edge блокируют «пустой» UA → 403; curl-like проходит стабильнее.
        "User-Agent": "NeuroMuleBot/1.0 (+https://neuromule.bot)",
        "Accept": "image/*,*/*",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"

    logger.info(
        "pollinations flux: prompt_len=%s url_host=%s auth=%s",
        len((prompt or "").strip()),
        "gen" if key else "legacy",
        bool(key),
    )
    data: bytes | None = None
    try:
        async with asyncio.timeout(POLLINATIONS_TIMEOUT_SEC):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(POLLINATIONS_TIMEOUT_SEC, connect=15.0),
                follow_redirects=True,
                headers=headers,
            ) as client:
                async with client.stream("GET", url) as response:
                    status = int(response.status_code)
                    if status != 200:
                        metrics.incr(
                            "pollinations.image.failed",
                            labels={"reason": f"http_{status}"},
                        )
                        snippet = b""
                        async for chunk in response.aiter_bytes():
                            snippet += chunk
                            if len(snippet) > 240:
                                break
                        logger.warning(
                            "pollinations flux HTTP %s body=%s",
                            status,
                            snippet[:200],
                        )
                        raise ExternalApiError("Pollinations", f"HTTP {status}")

                    buffer = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=DEFAULT_CHUNK_SIZE):
                        if not chunk:
                            continue
                        if len(buffer) + len(chunk) > DEFAULT_MAX_BYTES:
                            metrics.incr(
                                "pollinations.image.failed",
                                labels={"reason": "too_big"},
                            )
                            raise ExternalApiError("Pollinations", "response too large")
                        buffer.extend(chunk)
                    data = bytes(buffer)
    except TimeoutError as exc:
        metrics.incr("pollinations.image.failed", labels={"reason": "timeout"})
        raise ExternalApiError("Pollinations", "Flux: timeout") from exc
    except ExternalApiError:
        raise
    except httpx.HTTPError as exc:
        metrics.incr("pollinations.image.failed", labels={"reason": "http"})
        logger.warning("pollinations flux http error: %s", exc)
        raise ExternalApiError("Pollinations", f"Flux: {exc}") from exc

    if not data:
        metrics.incr("pollinations.image.failed", labels={"reason": "empty"})
        raise ExternalApiError("Pollinations", "Flux: пустой ответ")
    if not _is_valid_image_bytes(data):
        metrics.incr("pollinations.image.failed", labels={"reason": "invalid_image"})
        snippet = data[:120].decode("utf-8", errors="replace")
        logger.warning("pollinations flux non-image body: %s", snippet)
        raise ExternalApiError("Pollinations", "Flux: invalid image bytes")

    metrics.incr("pollinations.image.ok")
    metrics.observe("pollinations.image.bytes", len(data))
    return GeminiImageResult(url=None, data=data)
