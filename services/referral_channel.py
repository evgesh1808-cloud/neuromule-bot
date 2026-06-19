"""Реферальный бонус +2 💎 при активации друга на экране подписки на канал.

Антифрод для FREE: пригласитель на тарифе FREE может получить кристаллы
максимум за ``FREE_INVITER_MONTHLY_CAP`` приглашённых друзей в календарный
месяц. Платные тарифы (MINI/SMART/ULTRA) этим лимитом не ограничены.
"""

from __future__ import annotations

from datetime import date

import aiosqlite

from services import repository
from services.billing.crystals_balance import migrate_crystal_split_columns

FREE_INVITER_MONTHLY_CAP = 50


def _month_start_iso() -> str:
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}-01"


async def _count_paid_this_month(db: aiosqlite.Connection, inviter_id: int) -> int:
    async with db.execute(
        """
        SELECT COUNT(*) FROM referrals
        WHERE inviter_id = ?
          AND COALESCE(channel_bonus_paid, 0) = 1
          AND substr(COALESCE(created_at, ''), 1, 7) = substr(?, 1, 7)
        """,
        (inviter_id, _month_start_iso()),
    ) as cur:
        row = await cur.fetchone()
    return int(row[0] or 0)


async def grant_referral_channel_activation_bonus(
    invited_user_id: int,
    bonus: int = 2,
) -> int | None:
    """
    Начисляет ``buy_crystals`` пригласителю один раз за уникального реферала.

    Возвращает ``inviter_id``, если бонус выдан, иначе ``None``.
    """
    if bonus <= 0:
        return None
    async with aiosqlite.connect(repository.DB_PATH) as db:
        await migrate_crystal_split_columns(db)
        async with db.execute(
            """
            SELECT inviter_id, COALESCE(channel_bonus_paid, 0)
            FROM referrals WHERE invited_id = ?
            """,
            (invited_user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row or int(row[1]):
            return None
        inviter_id = int(row[0])

        async with db.execute(
            "SELECT tariff FROM users WHERE id = ?",
            (inviter_id,),
        ) as cur:
            tariff_row = await cur.fetchone()
        inviter_tariff = str(tariff_row[0] if tariff_row else "FREE").upper()
        if inviter_tariff in ("FREE", ""):
            paid_this_month = await _count_paid_this_month(db, inviter_id)
            if paid_this_month >= FREE_INVITER_MONTHLY_CAP:
                return None

        await db.execute(
            """
            UPDATE users SET
                buy_crystals = COALESCE(buy_crystals, 0) + ?,
                crystals = COALESCE(sub_crystals, 0) + COALESCE(buy_crystals, 0) + ?,
                balance = COALESCE(sub_crystals, 0) + COALESCE(buy_crystals, 0) + ?,
                balance_crystals = COALESCE(sub_crystals, 0) + COALESCE(buy_crystals, 0) + ?
            WHERE id = ?
            """,
            (bonus, bonus, bonus, bonus, inviter_id),
        )
        await db.execute(
            "UPDATE referrals SET channel_bonus_paid = 1 WHERE invited_id = ?",
            (invited_user_id,),
        )
        await db.commit()
    return inviter_id
