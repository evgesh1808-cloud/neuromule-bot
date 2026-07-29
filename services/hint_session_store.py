"""L2 SQLite persistence for HintSession (survives pm2 restart).

L1 RAM остаётся в ``services.standard_suggested_replies._HINT_SESSIONS``.
Этот модуль — тонкий aiosqlite API; hydration делает caller.
"""

from __future__ import annotations

import json
import logging
import time
import asyncio
from typing import Any, Sequence

import aiosqlite

logger = logging.getLogger(__name__)

# Кнопки в истории чата живут дольше одного рестарта процесса.
DEFAULT_HINT_SESSION_TTL_SEC = 7 * 24 * 3600


def _db_path() -> str:
    # Читаем на каждый вызов — тесты monkeypatch'ят ``repository.DB_PATH``.
    from services import repository

    return str(repository.DB_PATH)


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
    expires = now + max(60.0, float(ttl_sec))
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
                uid,
                int(user_id),
                int(message_id) if message_id is not None else None,
                body or "",
                labels_json,
                root_user_prompt or "",
                now,
                expires,
            ),
        )
        await db.commit()


async def bind_hint_session_message(action_uuid: str, message_id: int) -> None:
    """UPDATE message_id после успешного send в Telegram."""
    uid = (action_uuid or "").strip()
    if not uid:
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
    """``DELETE FROM hint_sessions WHERE expires_at < ?``. Возвращает число строк."""
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
    logger.info(
        "hint_sessions: gc loop started interval=%ss ttl=%ss",
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
    """Только тесты. Нет таблицы — no-op (миграция ещё не прогнана)."""
    try:
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute("DELETE FROM hint_sessions")
            await db.commit()
    except Exception:
        return
