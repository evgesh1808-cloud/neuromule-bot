"""Pollinations.ai — бесплатный Flux для FREE-tier фото.

Актуально (2026): ``gen.pollinations.ai`` требует API-ключ (иначе HTTP 401).
Без ключа используем рабочий legacy-endpoint ``image.pollinations.ai/prompt/...``.
С ``POLLINATIONS_API_KEY`` — новый gen-endpoint + Bearer.
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

# Legacy: без ключа, сейчас отвечает 200 + JPEG.
POLLINATIONS_IMAGE_BASE_LEGACY = "https://image.pollinations.ai/prompt"
# Новый API: нужен sk_/pk_ с enter.pollinations.ai.
POLLINATIONS_IMAGE_BASE_GEN = "https://gen.pollinations.ai/image"
POLLINATIONS_FLUX_MODEL = "flux"
DEFAULT_IMAGE_SIZE = 1024
MAX_PROMPT_CHARS = 1500
POLLINATIONS_TIMEOUT_SEC = 180.0


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

    metrics.incr("pollinations.image.ok")
    metrics.observe("pollinations.image.bytes", len(data))
    return GeminiImageResult(url=None, data=data)
