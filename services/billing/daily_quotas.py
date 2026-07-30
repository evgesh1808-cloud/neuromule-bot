"""Суточные квоты FREE Nano Banana: user_daily_quotas + глобальный предохранитель.

Ключ пользовательской квоты: ``free_banana_daily`` (1 фото/сутки, без ⚡).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite

from config import settings
from services import repository

logger = logging.getLogger(__name__)

FREE_PHOTO_QUOTA_KEY = "free_banana_daily"
GLOBAL_FREE_IMAGE_QUOTA_KEY = "free_image_global"
GLOBAL_QUOTA_USER_ID = 0

_MOSCOW_FALLBACK = timezone(timedelta(hours=3))


def _resolve_quota_tz() -> ZoneInfo | timezone:
    """Europe/Moscow на Windows без tzdata — фиксированный UTC+3."""
    key = getattr(settings, "quota_timezone", "Europe/Moscow") or "Europe/Moscow"
    try:
        return ZoneInfo(key)
    except (ZoneInfoNotFoundError, ModuleNotFoundError, ValueError):
        if key == "Europe/Moscow":
            logger.warning("tzdata unavailable for %s; using UTC+3 fallback", key)
            return _MOSCOW_FALLBACK
        logger.warning("tzdata unavailable for %s; using UTC", key)
        return timezone.utc


_QUOTA_TZ = _resolve_quota_tz()


def quota_day(*, now: datetime | None = None) -> str:
    """Календарный день квот (Europe/Moscow по умолчанию)."""
    ts = now or datetime.now(tz=_QUOTA_TZ)
    return ts.date().isoformat()


def free_photo_daily_limit() -> int:
    return max(1, int(settings.free_daily_photo_limit))


def global_free_image_daily_cap() -> int:
    return max(1, int(settings.global_free_image_daily_cap))


@dataclass(frozen=True)
class QuotaSnapshot:
    quota_key: str
    day: str
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit


async def ensure_quota_schema(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_daily_quotas (
            user_id     INTEGER NOT NULL,
            quota_key   TEXT    NOT NULL,
            quota_date  TEXT    NOT NULL,
            used_count  INTEGER NOT NULL DEFAULT 0,
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, quota_key, quota_date)
        )
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_udq_user_key_date
        ON user_daily_quotas (user_id, quota_key, quota_date DESC)
        """
    )


async def get_quota_snapshot(
    user_id: int,
    quota_key: str,
    *,
    day: str | None = None,
    limit: int | None = None,
) -> QuotaSnapshot:
    d = day or quota_day()
    if limit is None:
        limit = (
            free_photo_daily_limit()
            if quota_key == FREE_PHOTO_QUOTA_KEY
            else global_free_image_daily_cap()
        )
    async with aiosqlite.connect(repository.DB_PATH) as db:
        await ensure_quota_schema(db)
        async with db.execute(
            """
            SELECT used_count FROM user_daily_quotas
            WHERE user_id = ? AND quota_key = ? AND quota_date = ?
            """,
            (user_id, quota_key, d),
        ) as cur:
            row = await cur.fetchone()
    used = int(row[0]) if row else 0
    return QuotaSnapshot(quota_key=quota_key, day=d, used=used, limit=limit)


async def get_free_photo_snapshot(user_id: int, *, day: str | None = None) -> QuotaSnapshot:
    return await get_quota_snapshot(
        user_id,
        FREE_PHOTO_QUOTA_KEY,
        day=day,
        limit=free_photo_daily_limit(),
    )


async def get_global_free_image_snapshot(*, day: str | None = None) -> QuotaSnapshot:
    return await get_quota_snapshot(
        GLOBAL_QUOTA_USER_ID,
        GLOBAL_FREE_IMAGE_QUOTA_KEY,
        day=day,
        limit=global_free_image_daily_cap(),
    )


async def get_remaining_global_banana_slots(*, day: str | None = None) -> int:
    """
    Скрытый остаток глобального FREE-пула (для биллинга/алертов, не для UI).

    Источник — атомарный ``user_daily_quotas`` (ключ ``free_image_global``).
    """
    snap = await get_global_free_image_snapshot(day=day)
    return int(snap.remaining)


async def _increment_quota_locked(
    db: aiosqlite.Connection,
    user_id: int,
    quota_key: str,
    quota_date: str,
    limit: int,
) -> bool:
    """CAS-increment внутри BEGIN IMMEDIATE. True если слот взят."""
    async with db.execute(
        """
        SELECT used_count FROM user_daily_quotas
        WHERE user_id = ? AND quota_key = ? AND quota_date = ?
        """,
        (user_id, quota_key, quota_date),
    ) as cur:
        row = await cur.fetchone()
    used = int(row[0]) if row else 0
    if used >= limit:
        return False
    if row is None:
        cur = await db.execute(
            """
            INSERT INTO user_daily_quotas (user_id, quota_key, quota_date, used_count, updated_at)
            VALUES (?, ?, ?, 1, datetime('now'))
            """,
            (user_id, quota_key, quota_date),
        )
        return cur.rowcount == 1
    cur = await db.execute(
        """
        UPDATE user_daily_quotas
        SET used_count = used_count + 1,
            updated_at = datetime('now')
        WHERE user_id = ? AND quota_key = ? AND quota_date = ?
          AND used_count = ?
          AND used_count < ?
        """,
        (user_id, quota_key, quota_date, used, limit),
    )
    return cur.rowcount == 1


async def try_consume_free_photo_quota(user_id: int, *, day: str | None = None) -> str | None:
    """
    Атомарно резервирует FREE-слот пользователя + глобальный счётчик.

    Returns:
        None — слот не взят (user limit / global cap / race).
        str — quota_date для refund.
    """
    d = day or quota_day()
    user_lim = free_photo_daily_limit()
    global_lim = global_free_image_daily_cap()
    async with aiosqlite.connect(repository.DB_PATH) as db:
        await ensure_quota_schema(db)
        await db.execute("BEGIN IMMEDIATE")
        try:
            if not await _increment_quota_locked(
                db, GLOBAL_QUOTA_USER_ID, GLOBAL_FREE_IMAGE_QUOTA_KEY, d, global_lim
            ):
                await db.execute("ROLLBACK")
                return None
            if not await _increment_quota_locked(
                db, user_id, FREE_PHOTO_QUOTA_KEY, d, user_lim
            ):
                await _decrement_quota_locked(
                    db, GLOBAL_QUOTA_USER_ID, GLOBAL_FREE_IMAGE_QUOTA_KEY, d
                )
                await db.execute("ROLLBACK")
                return None
            await db.commit()
            return d
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def _decrement_quota_locked(
    db: aiosqlite.Connection,
    user_id: int,
    quota_key: str,
    quota_date: str,
) -> None:
    await db.execute(
        """
        UPDATE user_daily_quotas
        SET used_count = CASE WHEN used_count > 0 THEN used_count - 1 ELSE 0 END,
            updated_at = datetime('now')
        WHERE user_id = ? AND quota_key = ? AND quota_date = ?
        """,
        (user_id, quota_key, quota_date),
    )


async def refund_free_photo_quota_on_connection(
    db: aiosqlite.Connection,
    user_id: int,
    *,
    quota_date: str | None = None,
) -> None:
    """Откат user + global слота внутри уже открытой write-транзакции."""
    d = quota_date or quota_day()
    await _decrement_quota_locked(db, user_id, FREE_PHOTO_QUOTA_KEY, d)
    await _decrement_quota_locked(
        db, GLOBAL_QUOTA_USER_ID, GLOBAL_FREE_IMAGE_QUOTA_KEY, d
    )


async def refund_free_photo_quota(
    user_id: int,
    *,
    quota_date: str | None = None,
) -> None:
    """Откат user + global слота после failed generation."""
    d = quota_date or quota_day()
    async with aiosqlite.connect(repository.DB_PATH) as db:
        await ensure_quota_schema(db)
        await db.execute("BEGIN IMMEDIATE")
        await refund_free_photo_quota_on_connection(db, user_id, quota_date=d)
        await db.commit()
    logger.info("refunded free photo quota user_id=%s date=%s", user_id, d)


async def purge_stale_quota_rows(*, keep_days: int = 14) -> int:
    """Опциональная уборка старых строк (не влияет на лимиты)."""
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    async with aiosqlite.connect(repository.DB_PATH) as db:
        await ensure_quota_schema(db)
        cur = await db.execute(
            "DELETE FROM user_daily_quotas WHERE quota_date < ?",
            (cutoff,),
        )
        await db.commit()
        return int(cur.rowcount or 0)
