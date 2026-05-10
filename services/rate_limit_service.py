"""
Rate limit для чата: Redis (фиксированное окно по минутам) или SQLite в основной БД.

In-memory слой удалён — лимит переживает рестарт процесса (SQLite) или шарится между инстансами (Redis).
"""

from __future__ import annotations

import logging

from config import Settings
from services import repository as repo

logger = logging.getLogger(__name__)


async def allow_request(settings: Settings, user_id: int, max_per_minute: int) -> bool:
    if max_per_minute <= 0:
        return True
    url = (settings.redis_url or "").strip()
    if url:
        try:
            return await _redis_allow(url, user_id, max_per_minute)
        except ImportError:
            logger.warning("redis пакет не установлен, rate limit через SQLite")
        except Exception:
            logger.exception("Redis rate limit failed, fallback SQLite")
    return await repo.rate_limit_allow(user_id, max_per_minute)


async def rollback_last(settings: Settings, user_id: int) -> None:
    url = (settings.redis_url or "").strip()
    if url:
        try:
            await _redis_rollback(url, user_id)
            return
        except ImportError:
            pass
        except Exception:
            logger.debug("Redis rate limit rollback skipped", exc_info=True)
    await repo.rate_limit_rollback_last(user_id)


async def _redis_allow(url: str, user_id: int, max_per_minute: int) -> bool:
    import time

    import redis.asyncio as redis

    bucket = int(time.time()) // 60
    key = f"nm:rl:{user_id}:{bucket}"
    client = redis.from_url(url, encoding="utf-8", decode_responses=True)
    try:
        n = await client.incr(key)
        if n == 1:
            await client.expire(key, 120)
        return n <= max_per_minute
    finally:
        await client.aclose()


async def _redis_rollback(url: str, user_id: int) -> None:
    import time

    import redis.asyncio as redis

    bucket = int(time.time()) // 60
    key = f"nm:rl:{user_id}:{bucket}"
    client = redis.from_url(url, encoding="utf-8", decode_responses=True)
    try:
        v = await client.decr(key)
        if v < 0:
            await client.delete(key)
    finally:
        await client.aclose()
