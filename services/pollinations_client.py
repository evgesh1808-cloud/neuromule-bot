"""Pollinations.ai — бесплатный Flux Schnell для FREE-tier фото (без Replicate).

GET ``gen.pollinations.ai/image/{prompt}?model=flux`` — без API-ключа.
Загрузка через ``stream_download_to_bytes`` (PR-J): защита RAM + метрики.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import httpx

from services import metrics
from services.api_resilience import ExternalApiError
from services.gemini_image_client import GeminiImageResult
from services.streaming_download import DEFAULT_MAX_BYTES, stream_download_to_bytes

logger = logging.getLogger(__name__)

POLLINATIONS_IMAGE_BASE = "https://gen.pollinations.ai/image"
POLLINATIONS_FLUX_MODEL = "flux"
DEFAULT_IMAGE_SIZE = 1024
# URL-лимит + разумный потолок промпта для FREE.
MAX_PROMPT_CHARS = 1500
POLLINATIONS_TIMEOUT_SEC = 180.0


def build_pollinations_flux_url(
    prompt: str,
    *,
    width: int = DEFAULT_IMAGE_SIZE,
    height: int = DEFAULT_IMAGE_SIZE,
) -> str:
    """Собирает URL генерации Flux Schnell (Pollinations, model=flux)."""
    text = (prompt or "").strip()[:MAX_PROMPT_CHARS]
    if not text:
        raise ExternalApiError("Pollinations", "пустой промпт")
    encoded = quote(text, safe="")
    return (
        f"{POLLINATIONS_IMAGE_BASE}/{encoded}"
        f"?model={POLLINATIONS_FLUX_MODEL}"
        f"&width={int(width)}&height={int(height)}"
        f"&nologo=true&enhance=false"
    )


async def generate_flux_schnell_image(prompt: str) -> GeminiImageResult:
    """
    Генерирует изображение Flux Schnell через Pollinations.

    Возвращает ``GeminiImageResult(data=...)`` — байты для ``send_photo``.
    """
    url = build_pollinations_flux_url(prompt)
    logger.info(
        "pollinations flux: prompt_len=%s url_len=%s",
        len((prompt or "").strip()),
        len(url),
    )
    try:
        async with asyncio.timeout(POLLINATIONS_TIMEOUT_SEC):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(POLLINATIONS_TIMEOUT_SEC, connect=15.0),
                follow_redirects=True,
            ) as client:
                data = await stream_download_to_bytes(
                    client,
                    url,
                    max_bytes=DEFAULT_MAX_BYTES,
                    source="pollinations_flux",
                )
    except TimeoutError as exc:
        metrics.incr("pollinations.image.failed", labels={"reason": "timeout"})
        raise ExternalApiError("Pollinations", "Flux Schnell: timeout") from exc
    except httpx.HTTPError as exc:
        metrics.incr("pollinations.image.failed", labels={"reason": "http"})
        logger.warning("pollinations flux http error: %s", exc)
        raise ExternalApiError("Pollinations", f"Flux Schnell: {exc}") from exc

    if not data:
        metrics.incr("pollinations.image.failed", labels={"reason": "empty"})
        raise ExternalApiError("Pollinations", "Flux Schnell: пустой ответ")

    metrics.incr("pollinations.image.ok")
    metrics.observe("pollinations.image.bytes", len(data))
    return GeminiImageResult(url=None, data=data)
