"""
Rate limit и chat-lock для чата: Redis или SQLite в основной БД.

In-memory слой удалён — лимит/лок переживают рестарт (SQLite) или шарятся между инстансами (Redis).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

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


async def try_acquire_chat_lock(settings: Settings, user_id: int, ttl_sec: float) -> bool:
    """SET NX EX: True если лок взят. TTL страхует зависания (FREE cascade timeout)."""
    ttl = max(1, int(ttl_sec))
    url = (settings.redis_url or "").strip()
    if url:
        try:
            return await _redis_acquire_chat_lock(url, user_id, ttl)
        except ImportError:
            logger.warning("redis пакет не установлен, chat lock через SQLite")
        except Exception:
            logger.exception("Redis chat lock failed, fallback SQLite")
    return await repo.chat_lock_acquire(user_id, ttl)


async def release_chat_lock(settings: Settings, user_id: int) -> None:
    url = (settings.redis_url or "").strip()
    if url:
        try:
            await _redis_release_chat_lock(url, user_id)
            return
        except ImportError:
            pass
        except Exception:
            logger.debug("Redis chat lock release skipped", exc_info=True)
    await repo.chat_lock_release(user_id)


# Кулдаун предупреждения «ещё отвечаю» (антиспам повторных кликов).
_BUSY_NOTICE_COOLDOWN_SEC = 2
_BUSY_NOTICE_MSG_TTL_SEC = 30
_BUSY_NOTICE_UNTIL: dict[int, float] = {}
_BUSY_NOTICE_MSG_ID: dict[int, int] = {}


async def claim_chat_busy_notice(
    settings: Settings,
    user_id: int,
    *,
    cooldown_sec: int = _BUSY_NOTICE_COOLDOWN_SEC,
) -> bool:
    """True — показать TXT_CHAT_BUSY; False — молча игнорировать повторный клик.

    Redis: ``SET lock_msg_cooldown:{uid} NX EX`` (атомарно).
    Без Redis: короткий in-memory TTL.
    """
    ttl = max(1, int(cooldown_sec))
    url = (settings.redis_url or "").strip()
    if url:
        try:
            return await _redis_claim_busy_notice(url, user_id, ttl)
        except ImportError:
            logger.warning("redis пакет не установлен, busy-notice cooldown in-memory")
        except Exception:
            logger.exception("Redis busy-notice cooldown failed, fallback in-memory")
    return _memory_claim_busy_notice(user_id, ttl)


def _memory_claim_busy_notice(user_id: int, ttl_sec: int) -> bool:
    import time

    now = time.monotonic()
    until = _BUSY_NOTICE_UNTIL.get(int(user_id), 0.0)
    if until > now:
        return False
    _BUSY_NOTICE_UNTIL[int(user_id)] = now + float(ttl_sec)
    return True


async def remember_chat_busy_message_id(
    settings: Settings,
    user_id: int,
    message_id: int,
    *,
    ttl_sec: int = _BUSY_NOTICE_MSG_TTL_SEC,
) -> None:
    """Сохраняет ``message_id`` предупреждения (``lock_msg_id:{uid}``, TTL 30с)."""
    uid = int(user_id)
    mid = int(message_id)
    ttl = max(1, int(ttl_sec))
    url = (settings.redis_url or "").strip()
    if url:
        try:
            await _redis_remember_busy_message_id(url, uid, mid, ttl)
            return
        except ImportError:
            pass
        except Exception:
            logger.debug("Redis lock_msg_id set failed", exc_info=True)
    _BUSY_NOTICE_MSG_ID[uid] = mid


async def pop_chat_busy_message_id(settings: Settings, user_id: int) -> int | None:
    """Забирает и очищает сохранённый ``message_id`` предупреждения."""
    uid = int(user_id)
    url = (settings.redis_url or "").strip()
    if url:
        try:
            mid = await _redis_pop_busy_message_id(url, uid)
            if mid is not None:
                return mid
        except ImportError:
            pass
        except Exception:
            logger.debug("Redis lock_msg_id pop failed", exc_info=True)
    return _BUSY_NOTICE_MSG_ID.pop(uid, None)


@asynccontextmanager
async def free_chat_lock(
    settings: Settings,
    user_id: int,
    *,
    enabled: bool,
    ttl_sec: float,
) -> AsyncIterator[bool]:
    """FREE parallel-guard. Yields False если лок уже занят; иначе True и release в finally."""
    if not enabled:
        yield True
        return
    if not await try_acquire_chat_lock(settings, user_id, ttl_sec):
        yield False
        return
    try:
        yield True
    finally:
        await release_chat_lock(settings, user_id)


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


async def _redis_acquire_chat_lock(url: str, user_id: int, ttl_sec: int) -> bool:
    import redis.asyncio as redis

    key = f"chat_lock:{user_id}"
    client = redis.from_url(url, encoding="utf-8", decode_responses=True)
    try:
        # SET NX EX — атомарно; True только если ключа не было.
        ok = await client.set(key, "1", nx=True, ex=ttl_sec)
        return bool(ok)
    finally:
        await client.aclose()


async def _redis_release_chat_lock(url: str, user_id: int) -> None:
    import redis.asyncio as redis

    key = f"chat_lock:{user_id}"
    client = redis.from_url(url, encoding="utf-8", decode_responses=True)
    try:
        await client.delete(key)
    finally:
        await client.aclose()


async def _redis_claim_busy_notice(url: str, user_id: int, ttl_sec: int) -> bool:
    import redis.asyncio as redis

    key = f"lock_msg_cooldown:{user_id}"
    client = redis.from_url(url, encoding="utf-8", decode_responses=True)
    try:
        # SET NX EX: True только при первом клике в окне кулдауна.
        ok = await client.set(key, "1", nx=True, ex=ttl_sec)
        return bool(ok)
    finally:
        await client.aclose()


async def _redis_remember_busy_message_id(
    url: str, user_id: int, message_id: int, ttl_sec: int
) -> None:
    import redis.asyncio as redis

    key = f"lock_msg_id:{user_id}"
    client = redis.from_url(url, encoding="utf-8", decode_responses=True)
    try:
        await client.set(key, str(int(message_id)), ex=ttl_sec)
    finally:
        await client.aclose()


async def _redis_pop_busy_message_id(url: str, user_id: int) -> int | None:
    import redis.asyncio as redis

    key = f"lock_msg_id:{user_id}"
    client = redis.from_url(url, encoding="utf-8", decode_responses=True)
    try:
        raw = await client.get(key)
        if raw is None:
            return None
        await client.delete(key)
        return int(raw)
    finally:
        await client.aclose()
