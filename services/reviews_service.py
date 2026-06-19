"""Сервис гейминфикации отзывов (NeuroMule 🐎⚡️).

Хранит пользовательские отзывы в SQLite, начисляет бонусные ⚡ при подаче и
обслуживает админ-модерацию (approve/reject). Канал-публикация одобренных
отзывов делегирована :mod:`services.gallery_service`.

Таблица ``user_reviews``:

* ``id`` — autoincrement primary key (используется как ``review_id`` в
  callback_data модерации и тегах канала).
* ``user_id`` — автор.
* ``kind`` — ``text`` / ``photo`` / ``video`` / ``document`` (для пересылки в
  админ-чат и в канал в правильном формате).
* ``file_id`` — Telegram file_id (для photo/video/document), nullable.
* ``content`` — текст или caption (для text/photo/video).
* ``status`` — ``pending`` / ``approved`` / ``rejected``.
* ``created_at`` / ``moderated_at`` — ISO-таймстемпы.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

import aiosqlite

from services import repository

logger = logging.getLogger(__name__)


ReviewKind = Literal["text", "photo", "video", "document"]
ReviewStatus = Literal["pending", "approved", "rejected"]


_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS user_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    file_id TEXT,
    content TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    moderated_at TEXT
)
"""


async def ensure_reviews_schema() -> None:
    """Создаёт таблицу при первом обращении. Идемпотентно."""

    async with aiosqlite.connect(repository.DB_PATH) as db:
        await db.execute(_SCHEMA_DDL)
        await db.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def submit_review(
    user_id: int,
    *,
    kind: ReviewKind,
    content: str,
    file_id: str | None = None,
) -> int:
    """Сохраняет отзыв в БД со статусом ``pending``. Возвращает ``review_id``."""

    await ensure_reviews_schema()
    text = (content or "").strip()
    async with aiosqlite.connect(repository.DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO user_reviews (user_id, kind, file_id, content, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (int(user_id), kind, file_id, text, _now_iso()),
        )
        await db.commit()
        return int(cur.lastrowid or 0)


async def grant_review_bonus(user_id: int, *, amount: int) -> bool:
    """Атомарно добавляет ``amount`` ⚡ на ``energy_paid`` пользователя.

    Возвращает ``True`` при успешном начислении. Совместимо со схемой,
    используемой в ``try_redeem_promo``: одновременно поддерживаем legacy
    поля ``energy`` / ``balance_energy``.
    """

    if amount <= 0:
        return False
    await repository.ensure_user(user_id)
    async with aiosqlite.connect(repository.DB_PATH) as db:
        await db.execute(
            """
            UPDATE users SET
                energy_paid = COALESCE(energy_paid, 0) + ?,
                energy = COALESCE(energy_free, 0) + COALESCE(energy_paid, 0) + ?,
                balance_energy = COALESCE(energy_free, 0) + COALESCE(energy_paid, 0) + ?
            WHERE id = ?
            """,
            (amount, amount, amount, int(user_id)),
        )
        await db.commit()
    return True


async def get_review(review_id: int) -> dict | None:
    """Возвращает отзыв по id или ``None``."""

    await ensure_reviews_schema()
    async with aiosqlite.connect(repository.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_reviews WHERE id = ?", (int(review_id),)
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


async def set_review_status(review_id: int, status: ReviewStatus) -> bool:
    """Атомарно переводит статус отзыва. ``True`` при изменении."""

    await ensure_reviews_schema()
    async with aiosqlite.connect(repository.DB_PATH) as db:
        cur = await db.execute(
            "UPDATE user_reviews SET status = ?, moderated_at = ? WHERE id = ?",
            (status, _now_iso(), int(review_id)),
        )
        await db.commit()
        return cur.rowcount > 0


__all__ = (
    "ReviewKind",
    "ReviewStatus",
    "ensure_reviews_schema",
    "submit_review",
    "grant_review_bonus",
    "get_review",
    "set_review_status",
)
