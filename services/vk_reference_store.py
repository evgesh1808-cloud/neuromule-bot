"""
In-memory кэш референс-фото VK: скачивание сразу при получении, ключ — peer_id.

VK CDN URL в ``sizes[].url`` может протухнуть (query/expiry). Двухшаговый i2i
(фото → промпт позже) хранит байты, а не URL.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from services.streaming_download import stream_download_to_bytes

logger = logging.getLogger(__name__)

DEFAULT_TTL_SEC = 900.0
_MAX_CACHED_PEERS = 2048

_peer_locks: dict[int, asyncio.Lock] = {}
_pending: dict[int, "CachedVkPhotoRef"] = {}


@dataclass(frozen=True, slots=True)
class CachedVkPhotoRef:
    data: bytes
    mime: str
    peer_id: int
    expires_at: float


def _peer_lock(peer_id: int) -> asyncio.Lock:
    lock = _peer_locks.get(peer_id)
    if lock is None:
        lock = asyncio.Lock()
        _peer_locks[peer_id] = lock
    return lock


def _mime_from_url(url: str) -> str:
    low = url.lower()
    if low.endswith(".png"):
        return "image/png"
    if low.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def _evict_expired(now: float | None = None) -> None:
    ts = time.monotonic() if now is None else now
    expired = [pid for pid, ref in _pending.items() if ref.expires_at <= ts]
    for pid in expired:
        _pending.pop(pid, None)


def _trim_if_needed() -> None:
    if len(_pending) <= _MAX_CACHED_PEERS:
        return
    # Сначала протухшие, затем самые старые.
    _evict_expired()
    if len(_pending) <= _MAX_CACHED_PEERS:
        return
    ordered = sorted(_pending.items(), key=lambda item: item[1].expires_at)
    for pid, _ in ordered[: len(_pending) - _MAX_CACHED_PEERS]:
        _pending.pop(pid, None)


async def cache_vk_photo_from_url(
    peer_id: int,
    url: str,
    *,
    ttl_sec: float = DEFAULT_TTL_SEC,
) -> CachedVkPhotoRef | None:
    """Скачивает фото немедленно и кладёт в кэш по ``peer_id``."""
    clean_url = (url or "").strip()
    if peer_id <= 0 or not clean_url:
        return None

    async with _peer_lock(peer_id):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
                data = await stream_download_to_bytes(client, clean_url, source="vk_photo_ref")
        except httpx.HTTPError:
            logger.warning("vk photo ref download failed peer_id=%s", peer_id, exc_info=True)
            return None

        if not data:
            logger.warning("vk photo ref empty peer_id=%s url=%s", peer_id, clean_url[:120])
            return None

        ref = CachedVkPhotoRef(
            data=data,
            mime=_mime_from_url(clean_url),
            peer_id=peer_id,
            expires_at=time.monotonic() + ttl_sec,
        )
        _pending[peer_id] = ref
        _trim_if_needed()
        logger.info("vk photo ref cached peer_id=%s bytes=%s ttl=%ss", peer_id, len(data), int(ttl_sec))
        return ref


def peek_pending_vk_photo(peer_id: int) -> CachedVkPhotoRef | None:
    ref = _pending.get(peer_id)
    if ref is None:
        return None
    if ref.expires_at <= time.monotonic():
        _pending.pop(peer_id, None)
        return None
    return ref


def take_pending_vk_photo(peer_id: int) -> CachedVkPhotoRef | None:
    ref = peek_pending_vk_photo(peer_id)
    if ref is not None:
        _pending.pop(peer_id, None)
    return ref


def clear_pending_vk_photo(peer_id: int) -> None:
    _pending.pop(peer_id, None)
