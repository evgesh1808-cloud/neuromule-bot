"""Утилита chunked-загрузки медиа с жёстким лимитом размера.

Зачем: API кросс-постинга (VK photos, MAX App, в будущем — VK video и
voice cloning) принимают файл как `multipart/form-data`. Telegram-CDN или
Replicate могут выдать медиа произвольного размера. Если читать через
``client.get(url).content`` — всё тело влетит в RAM ноды разом.

Решение: ``client.stream("GET", url)`` + `aiter_bytes(chunk_size)` →
поточное накопление в `bytearray`, проверка `len > max_bytes` на каждой
итерации, ранний обрыв при превышении.

Контракт:

* ``max_bytes`` — жёсткий лимит. Превышение → `None`, без exception.
* Возвращаемое значение — ``bytes`` (готово для `multipart` body) или
  ``None`` при любой неуспешной ситуации (status != 200, превышение
  лимита, сетевой сбой).
* Метрика ``download.bytes{source}`` (гистограмма) пишется при успехе;
  ``download.too_big{source}`` (counter) — при превышении лимита.
* generic ``except Exception`` отсутствует — ловим конкретно
  ``httpx.HTTPError`` (включает ``TimeoutException``, ``NetworkError``,
  ``RemoteProtocolError`` и т.д.).

Дизайн-инварианты:

* НЕ блокирующих операций на основном потоке (httpx async-stream);
* НЕ создаёт собственный ``AsyncClient`` — переиспользует переданный
  (callsite контролирует timeout / connection pool / retries);
* НЕ записывает в файл — только in-memory bytearray. Для очень
  больших файлов (>200 МБ) понадобится отдельный `to_temp_file` helper,
  его добавим, когда API кросс-постинга это потребует.
"""
from __future__ import annotations

import logging

import httpx

from services import metrics

logger = logging.getLogger(__name__)


DEFAULT_CHUNK_SIZE = 64 * 1024
"""По умолчанию 64 KB — компромисс между накладными расходами на цикл
и плавностью cooperative-multitasking'а."""

DEFAULT_MAX_BYTES = 20 * 1024 * 1024
"""20 МБ — типичный безопасный лимит для фото-аплоада в VK / MAX App.
VK photos.save принимает ~50 МБ, но мы отрезаем раньше: качество фото
в галерее не страдает, RAM ноды защищён."""


async def stream_download_to_bytes(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    source: str = "unknown",
    timeout: float | None = None,
) -> bytes | None:
    """Скачать ``url`` поточно, накапливая в RAM до ``max_bytes``.

    Args:
        client: переиспользуемый ``httpx.AsyncClient`` (callsite владелец).
        url: HTTPS-URL медиа (Telegram CDN / Replicate / Suno и т.п.).
        max_bytes: жёсткий лимит размера. Превышение → ``None``.
        chunk_size: размер чанка в байтах.
        source: метка для метрик (например ``"vk_photo"``).
        timeout: переопределение клиентского timeout'а (опционально).

    Returns:
        ``bytes`` при успехе (длина ≤ ``max_bytes``), иначе ``None``.

    Метрики:
        * ``download.bytes{source}`` — гистограмма размера успешных
          загрузок;
        * ``download.too_big{source}`` — счётчик превышений лимита;
        * ``download.http_error{source,status}`` — счётчик не-200
          ответов;
        * ``download.network_error{source}`` — счётчик сетевых сбоев.
    """

    kwargs: dict = {}
    if timeout is not None:
        kwargs["timeout"] = float(timeout)

    try:
        async with client.stream("GET", url, **kwargs) as response:
            if response.status_code != 200:
                metrics.incr(
                    "download.http_error",
                    {"source": source, "status": str(response.status_code)},
                )
                logger.warning(
                    "stream_download: HTTP %s for source=%s url=%s",
                    response.status_code,
                    source,
                    url[:100],
                )
                return None

            buffer = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                if not chunk:
                    continue
                if len(buffer) + len(chunk) > max_bytes:
                    metrics.incr("download.too_big", {"source": source})
                    logger.warning(
                        "stream_download: exceeds limit source=%s already=%s "
                        "next_chunk=%s max=%s url=%s",
                        source,
                        len(buffer),
                        len(chunk),
                        max_bytes,
                        url[:100],
                    )
                    return None
                buffer.extend(chunk)

            metrics.observe(
                "download.bytes", float(len(buffer)), {"source": source}
            )
            return bytes(buffer)

    except httpx.HTTPError as exc:
        metrics.incr("download.network_error", {"source": source})
        logger.warning(
            "stream_download: network error source=%s url=%s reason=%s",
            source,
            url[:100],
            str(exc)[:200],
        )
        return None


__all__ = (
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_MAX_BYTES",
    "stream_download_to_bytes",
)
