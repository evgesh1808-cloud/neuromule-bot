"""Прозрачный «чек операции» — добавляется в конец ответа ИИ."""

from __future__ import annotations

from services.billing.types import ChargeBreakdown, ChatRoutePlan, CurrencyKind, UserBillingState

_DIVIDER = "\n───────────────────\n"


def _role_label(role_id: str) -> str:
    from content.messages import TEXT_ROLES

    rid = (role_id or "standard").strip().lower()
    for label, role in TEXT_ROLES:
        if role == rid:
            return label
    return "Стандарт"


def _format_spent_from_charge(charge: ChargeBreakdown) -> str:
    if charge.crystals:
        return f"{charge.crystals} 💎"
    energy_total = (charge.energy_free or 0) + (charge.energy_paid or 0)
    if energy_total:
        return f"{energy_total} ⚡"
    if charge.used_photo_free_slot:
        return "FREE-слот 🖼️"
    return "0 ⚡"


def build_chat_receipt(
    plan: ChatRoutePlan,
    user_after: UserBillingState,
    role_id: str,
) -> str:
    """Чек для текстовых ответов ИИ."""
    role_label = _role_label(role_id)
    if plan.price_type is CurrencyKind.CRYSTALS and plan.crystal_cost:
        spent = f"{plan.crystal_cost} 💎"
    elif plan.price_type is CurrencyKind.ENERGY and plan.energy_cost:
        spent = f"{plan.energy_cost} ⚡"
    else:
        spent = "0 ⚡"
    return (
        f"{_DIVIDER}"
        f"🧾 Чек операции @NeuroMule_bot 🐎⚡️\n"
        f"• Списано: {spent} (Режим: {role_label})\n"
        f"• Остаток: {user_after.total_energy} ⚡ · {user_after.crystals} 💎"
    )


def build_media_receipt(
    charge: ChargeBreakdown,
    user_after: UserBillingState,
    feature_label: str,
) -> str:
    """Чек для медиа-операций (фото/видео/HD/музыка)."""
    return (
        f"{_DIVIDER}"
        f"🧾 Чек операции @NeuroMule_bot 🐎⚡️\n"
        f"• Списано: {_format_spent_from_charge(charge)} (Режим: {feature_label})\n"
        f"• Остаток: {user_after.total_energy} ⚡ · {user_after.crystals} 💎"
    )
