"""L2 persistence for HintSession (survives pm2 restart).

Production: Redis при ``REDIS_URL`` (TTL 7 дней, без блокировок SQLite).
Dev/pytest без Redis: fallback на SQLite (``hint_sessions`` в основной БД).

L1 RAM остаётся в ``services.standard_suggested_replies._HINT_SESSIONS``.
"""

from __future__ import annotations

import json
import logging
import time
import asyncio
from typing import Any, Sequence

import aiosqlite

logger = logging.getLogger(__name__)

REDIS_KEY_PREFIX = "hint_session:"

# Кнопки в истории чата живут дольше одного рестарта процесса.
DEFAULT_HINT_SESSION_TTL_SEC = 7 * 24 * 3600


def _redis_url() -> str:
    from config import settings

    return (getattr(settings, "redis_url", None) or "").strip()


def _use_redis() -> bool:
    return bool(_redis_url())


def _db_path() -> str:
    from services import repository

    return str(repository.DB_PATH)


def _redis_key(action_uuid: str) -> str:
    return f"{REDIS_KEY_PREFIX}{action_uuid}"


async def _redis_client():
    import redis.asyncio as redis

    return redis.from_url(_redis_url(), encoding="utf-8", decode_responses=True)


def _row_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    labels = payload.get("labels")
    if isinstance(labels, str):
        try:
            labels = json.loads(labels)
        except json.JSONDecodeError:
            labels = []
    if not isinstance(labels, list):
        labels = []
    message_id = payload.get("message_id")
    return {
        "action_uuid": str(payload.get("action_uuid") or ""),
        "user_id": int(payload["user_id"]),
        "message_id": int(message_id) if message_id is not None else None,
        "body": str(payload.get("body") or ""),
        "labels": tuple(str(x) for x in labels),
        "root_user_prompt": str(payload.get("root_user_prompt") or ""),
        "created_at": float(payload.get("created_at") or 0.0),
        "expires_at": float(payload.get("expires_at") or 0.0),
    }


async def _save_hint_session_redis(
    *,
    action_uuid: str,
    user_id: int,
    body: str,
    labels: Sequence[str],
    root_user_prompt: str,
    message_id: int | None,
    created_at: float,
    expires_at: float,
    ttl_sec: float,
) -> None:
    uid = action_uuid
    payload = {
        "action_uuid": uid,
        "user_id": int(user_id),
        "message_id": int(message_id) if message_id is not None else None,
        "body": body or "",
        "labels": list(labels),
        "root_user_prompt": root_user_prompt or "",
        "created_at": created_at,
        "expires_at": expires_at,
    }
    client = await _redis_client()
    try:
        await client.set(
            _redis_key(uid),
            json.dumps(payload, ensure_ascii=False),
            ex=int(max(60.0, ttl_sec)),
        )
    finally:
        await client.aclose()


async def _save_hint_session_sqlite(
    *,
    action_uuid: str,
    user_id: int,
    body: str,
    labels: Sequence[str],
    root_user_prompt: str,
    message_id: int | None,
    created_at: float,
    expires_at: float,
) -> None:
    labels_json = json.dumps(list(labels), ensure_ascii=False)
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT INTO hint_sessions (
                action_uuid, user_id, message_id, body, labels_json,
                root_user_prompt, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(action_uuid) DO UPDATE SET
                user_id = excluded.user_id,
                message_id = excluded.message_id,
                body = excluded.body,
                labels_json = excluded.labels_json,
                root_user_prompt = excluded.root_user_prompt,
                created_at = excluded.created_at,
                expires_at = excluded.expires_at
            """,
            (
                action_uuid,
                int(user_id),
                int(message_id) if message_id is not None else None,
                body or "",
                labels_json,
                root_user_prompt or "",
                created_at,
                expires_at,
            ),
        )
        await db.commit()


async def save_hint_session(
    *,
    action_uuid: str,
    user_id: int,
    body: str,
    labels: Sequence[str],
    root_user_prompt: str,
    message_id: int | None = None,
    ttl_sec: float = DEFAULT_HINT_SESSION_TTL_SEC,
) -> None:
    """INSERT OR REPLACE сессии по ``action_uuid``."""
    uid = (action_uuid or "").strip()
    if not uid:
        return
    now = time.time()
    ttl = max(60.0, float(ttl_sec))
    expires = now + ttl
    if _use_redis():
        await _save_hint_session_redis(
            action_uuid=uid,
            user_id=user_id,
            body=body,
            labels=labels,
            root_user_prompt=root_user_prompt,
            message_id=message_id,
            created_at=now,
            expires_at=expires,
            ttl_sec=ttl,
        )
        return
    await _save_hint_session_sqlite(
        action_uuid=uid,
        user_id=user_id,
        body=body,
        labels=labels,
        root_user_prompt=root_user_prompt,
        message_id=message_id,
        created_at=now,
        expires_at=expires,
    )


async def bind_hint_session_message(action_uuid: str, message_id: int) -> None:
    """UPDATE message_id после успешного send в Telegram."""
    uid = (action_uuid or "").strip()
    if not uid:
        return
    if _use_redis():
        client = await _redis_client()
        try:
            raw = await client.get(_redis_key(uid))
            if not raw:
                return
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("hint_session_store: bad redis json uuid=%s", uid)
                return
            if not isinstance(payload, dict):
                return
            payload["message_id"] = int(message_id)
            ttl = await client.ttl(_redis_key(uid))
            ex = int(ttl) if ttl and ttl > 0 else int(DEFAULT_HINT_SESSION_TTL_SEC)
            await client.set(
                _redis_key(uid),
                json.dumps(payload, ensure_ascii=False),
                ex=ex,
            )
        finally:
            await client.aclose()
        return

    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            UPDATE hint_sessions
            SET message_id = ?
            WHERE action_uuid = ?
            """,
            (int(message_id), uid),
        )
        await db.commit()


async def get_hint_session(action_uuid: str, *, user_id: int) -> dict[str, Any] | None:
    """Читает непросроченную сессию владельца. ``None`` если miss / чужой / expired."""
    uid = (action_uuid or "").strip()
    if not uid:
        return None
    now = time.time()

    if _use_redis():
        client = await _redis_client()
        try:
            raw = await client.get(_redis_key(uid))
        finally:
            await client.aclose()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("hint_session_store: bad redis json uuid=%s", uid)
            return None
        if not isinstance(payload, dict):
            return None
        if int(payload.get("user_id") or 0) != int(user_id):
            return None
        expires_at = float(payload.get("expires_at") or 0.0)
        if expires_at <= now:
            return None
        return _row_from_payload(payload)

    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(
            """
            SELECT action_uuid, user_id, message_id, body, labels_json,
                   root_user_prompt, created_at, expires_at
            FROM hint_sessions
            WHERE action_uuid = ? AND user_id = ? AND expires_at > ?
            """,
            (uid, int(user_id), now),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    try:
        labels = json.loads(row[4] or "[]")
    except json.JSONDecodeError:
        logger.warning("hint_session_store: bad labels_json uuid=%s", uid)
        labels = []
    if not isinstance(labels, list):
        labels = []
    return {
        "action_uuid": str(row[0]),
        "user_id": int(row[1]),
        "message_id": int(row[2]) if row[2] is not None else None,
        "body": str(row[3] or ""),
        "labels": tuple(str(x) for x in labels),
        "root_user_prompt": str(row[5] or ""),
        "created_at": float(row[6]),
        "expires_at": float(row[7]),
    }


async def clear_expired_hint_sessions(*, now: float | None = None) -> int:
    """GC просроченных сессий. Redis: TTL на ключах — sweep no-op (0)."""
    if _use_redis():
        return 0

    ts = time.time() if now is None else float(now)
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            "DELETE FROM hint_sessions WHERE expires_at < ?",
            (ts,),
        )
        deleted = int(cur.rowcount or 0)
        await db.commit()
    if deleted:
        logger.info("hint_sessions: gc removed=%s", deleted)
    return deleted


async def delete_expired_hint_sessions(*, limit: int = 500) -> int:
    """Совместимый alias: батчевый GC (lazy на create). Полный sweep — ``clear_expired_hint_sessions``."""
    _ = limit
    return await clear_expired_hint_sessions()


async def clear_expired_hint_sessions_loop(
    *,
    interval_sec: float = 24 * 3600,
) -> None:
    """Фоновый GC раз в сутки (старт + каждый tick). Запускать из telegram_bot."""
    backend = "redis" if _use_redis() else "sqlite"
    logger.info(
        "hint_sessions: gc loop started backend=%s interval=%ss ttl=%ss",
        backend,
        int(interval_sec),
        int(DEFAULT_HINT_SESSION_TTL_SEC),
    )
    while True:
        try:
            await clear_expired_hint_sessions()
        except Exception:
            logger.exception("hint_sessions: gc tick failed")
        try:
            await asyncio.sleep(max(60.0, float(interval_sec)))
        except asyncio.CancelledError:
            raise


async def clear_hint_sessions_for_tests() -> None:
    """Только тесты. Redis: SCAN+DEL; SQLite: DELETE."""
    if _use_redis():
        client = await _redis_client()
        deleted = 0
        try:
            async for key in client.scan_iter(match=f"{REDIS_KEY_PREFIX}*"):
                await client.delete(key)
                deleted += 1
        finally:
            await client.aclose()
        if deleted:
            logger.debug("hint_sessions: test clear redis removed=%s", deleted)
        return

    try:
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute("DELETE FROM hint_sessions")
            await db.commit()
    except Exception:
        return
