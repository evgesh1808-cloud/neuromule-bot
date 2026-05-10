"""Use-case: активация промокода (без UI — только БД и ключ ответа)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from services.repository import ensure_user, try_redeem_promo


class PromoOutcome(str, Enum):
    REDEEMED = "redeemed"
    UNKNOWN = "unknown"
    USED = "used"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True)
class PromoResult:
    """Результат ``run_promo_redeem``."""

    outcome: PromoOutcome
    bonus_energy: int = 0


async def run_promo_redeem(user_id: int, raw_code: str) -> PromoResult:
    """
    Вход:
        user_id — Telegram user id.
        raw_code — строка промокода (как ввёл пользователь).

    Возвращает:
        ``PromoResult`` с исходом и числом начисленной энергии при успехе.
    """
    await ensure_user(user_id)
    ok, key, bonus = await try_redeem_promo(user_id, raw_code)
    if ok:
        return PromoResult(outcome=PromoOutcome.REDEEMED, bonus_energy=bonus)
    if key == "used":
        return PromoResult(outcome=PromoOutcome.USED)
    if key == "exhausted":
        return PromoResult(outcome=PromoOutcome.EXHAUSTED)
    return PromoResult(outcome=PromoOutcome.UNKNOWN)
