"""Round-robin индекс провайдера Nano Banana FREE (Redis → SQLite fallback)."""

from __future__ import annotations

import logging

import aiosqlite

from config import settings
from services import repository

logger = logging.getLogger(__name__)

REDIS_KEY = "neuromule:free_image:provider_index"
SQLITE_KEY = "global_provider_index"


async def _ensure_sqlite_schema(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_runtime_kv (
            key   TEXT PRIMARY KEY,
            value INTEGER NOT NULL DEFAULT 0
        )
        """
    )


async def _next_index_sqlite() -> int:
    """Атомарный INCR в SQLite; возвращает индекс *до* инкремента (для выбора порядка)."""
    async with aiosqlite.connect(repository.DB_PATH) as db:
        await _ensure_sqlite_schema(db)
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT value FROM bot_runtime_kv WHERE key = ?",
            (SQLITE_KEY,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            idx = 0
            await db.execute(
                "INSERT INTO bot_runtime_kv (key, value) VALUES (?, 1)",
                (SQLITE_KEY,),
            )
        else:
            idx = int(row[0])
            await db.execute(
                "UPDATE bot_runtime_kv SET value = value + 1 WHERE key = ?",
                (SQLITE_KEY,),
            )
        await db.commit()
        return idx


async def _next_index_redis(url: str) -> int | None:
    try:
        import redis.asyncio as redis
    except ImportError:
        logger.warning("redis пакет не установлен, provider RR через SQLite")
        return None
    client = redis.from_url(url, encoding="utf-8", decode_responses=True)
    try:
        # INCR returns new value; we want index before increment → (n-1).
        n = int(await client.incr(REDIS_KEY))
        return n - 1
    except Exception:
        logger.exception("Redis provider RR failed, fallback SQLite")
        return None
    finally:
        await client.aclose()


async def next_provider_index() -> int:
    """
    Глобальный round-robin счётчик.

    Чётный → OpenRouter first; нечётный → Gemini first.
    """
    url = (getattr(settings, "redis_url", None) or "").strip()
    if url:
        idx = await _next_index_redis(url)
        if idx is not None:
            return idx
    return await _next_index_sqlite()


async def reset_provider_index_for_tests() -> None:
    """Только тесты: обнулить счётчик в SQLite."""
    async with aiosqlite.connect(repository.DB_PATH) as db:
        await _ensure_sqlite_schema(db)
        await db.execute(
            "INSERT INTO bot_runtime_kv (key, value) VALUES (?, 0) "
            "ON CONFLICT(key) DO UPDATE SET value = 0",
            (SQLITE_KEY,),
        )
        await db.commit()
