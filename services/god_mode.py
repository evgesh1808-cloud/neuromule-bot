"""God Mode: супер-админы (ADMIN_IDS) обходят биллинг, лимиты совета дня и проверку канала."""

from __future__ import annotations

from config import settings
from services.billing.types import ChargeBreakdown, SpendResult

GOD_MODE_CHARGE_ID = "god_mode_skip"


def is_super_admin(user_id: int | None) -> bool:
    """True, если user_id в ``settings.admin_ids`` (env ``ADMIN_IDS``)."""
    if user_id is None:
        return False
    return user_id in set(settings.admin_ids)


def billing_bypass(user_id: int | None) -> bool:
    """Обход списаний и billing pre-check: ``GOD_MODE_ENABLED`` + супер-админ."""
    return bool(settings.god_mode_enabled) and is_super_admin(user_id)


def god_mode_charge() -> ChargeBreakdown:
    """Фиктивное списание без изменения баланса в БД."""
    return ChargeBreakdown(charge_id=GOD_MODE_CHARGE_ID)


def god_mode_spend_result() -> SpendResult:
    return SpendResult(ok=True, charge=god_mode_charge())


def is_god_mode_charge(charge_id: str | None) -> bool:
    return (charge_id or "") == GOD_MODE_CHARGE_ID
