"""Ежемесячное продление платных тарифов (cron / ручной запуск)."""

from __future__ import annotations

import logging
from datetime import date

import aiosqlite

from business_catalog import catalog
from services.billing.store import apply_tariff_period_renewal
from services.repository import DB_PATH

logger = logging.getLogger(__name__)

_PAID_TARIFFS = ("MINI", "SMART", "ULTRA")


def _pack_for_tariff(tariff: str) -> tuple[int, int] | None:
    key = tariff.strip().upper()
    spec = catalog.shop_packs.get(key)
    if not spec:
        return None
    return int(spec["energy_paid"]), int(spec["crystals"])


async def renew_due_subscriptions(*, today: str | None = None) -> int:
    """
    Пользователи с истёкшим ``subscription_ends_at``: сброс энергии/sub_crystals и новый пакет.
    ``buy_crystals`` не изменяются. Возвращает число обработанных пользователей.
    """
    day = today or date.today().isoformat()
    renewed = 0
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT id, UPPER(COALESCE(tariff, 'FREE')) AS tariff
            FROM users
            WHERE UPPER(COALESCE(tariff, 'FREE')) IN ('MINI', 'SMART', 'ULTRA')
              AND subscription_ends_at IS NOT NULL
              AND subscription_ends_at <= ?
            """,
            (day,),
        ) as cur:
            rows = await cur.fetchall()
    for user_id, tariff in rows:
        pack = _pack_for_tariff(str(tariff))
        if not pack:
            continue
        energy_grant, sub_crystals = pack
        await apply_tariff_period_renewal(
            int(user_id),
            tariff=str(tariff),
            energy_paid_grant=energy_grant,
            sub_crystals_grant=sub_crystals,
        )
        renewed += 1
        logger.info(
            "subscription_renewed user_id=%s tariff=%s energy=%s sub_crystals=%s",
            user_id,
            tariff,
            energy_grant,
            sub_crystals,
        )
    return renewed
