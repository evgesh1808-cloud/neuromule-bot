"""
Use-case: активация подарочного промокода.

Подарочные коды (gift codes) дают единоразовое начисление ресурсов:
энергия (`energy_paid`) и/или вечные кристаллы (`buy_crystals`). На стоимость
тарифов в магазине влияния НЕТ — цены всегда фиксированные.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from services.repository import ensure_user, try_redeem_promo


class PromoOutcome(str, Enum):
    """Возможные исходы активации подарочного промокода."""

    REDEEMED = "redeemed"
    TARIFF_BLOCKED = "tariff_blocked"
    UNKNOWN = "unknown"
    USED = "used"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True)
class PromoResult:
    """Результат ``run_promo_redeem``."""

    outcome: PromoOutcome
    bonus_energy: int = 0
    bonus_crystals: int = 0


async def run_promo_redeem(user_id: int, raw_code: str) -> PromoResult:
    """
    Активирует подарочный код и возвращает структурированный результат.

    Вход:
        user_id — Telegram user id.
        raw_code — строка кода (как ввёл пользователь).

    Возвращает:
        ``PromoResult`` с исходом и бонусами при успехе.
    """
    await ensure_user(user_id)
    ok, key, bonus_energy, bonus_crystals = await try_redeem_promo(user_id, raw_code)
    if ok:
        return PromoResult(
            outcome=PromoOutcome.REDEEMED,
            bonus_energy=bonus_energy,
            bonus_crystals=bonus_crystals,
        )
    if key == "tariff_blocked":
        return PromoResult(outcome=PromoOutcome.TARIFF_BLOCKED)
    if key == "used":
        return PromoResult(outcome=PromoOutcome.USED)
    if key == "exhausted":
        return PromoResult(outcome=PromoOutcome.EXHAUSTED)
    return PromoResult(outcome=PromoOutcome.UNKNOWN)
