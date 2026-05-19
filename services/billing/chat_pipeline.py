"""Маршрутизация текстового чата (OpenRouter)."""

from __future__ import annotations

from content.messages import PREMIUM_TEXT_ROLE_IDS
from services.billing import store
from services.billing.pricing import (
    CHAT_EXPERT_CRYSTALS,
    CHAT_EXPERT_ENERGY,
    CHAT_STANDARD_CRYSTALS,
    CHAT_STANDARD_ENERGY,
    FREE_CHAT_MODEL,
    PAID_CHAT_MODEL,
)
from services.billing.types import ChatRoutePlan, CurrencyKind, SpendFeature, TariffTier, UserBillingState


def _is_expert_role(role_type: str) -> bool:
    rid = (role_type or "standard").strip().lower()
    return rid in PREMIUM_TEXT_ROLE_IDS


def plan_text_chat(user: UserBillingState, role_type: str) -> ChatRoutePlan:
    """Рассчитать модель и стоимость без списания."""
    expert = _is_expert_role(role_type)
    tariff = user.current_tariff

    if tariff is TariffTier.FREE:
        if expert:
            return ChatRoutePlan(
                model_id=FREE_CHAT_MODEL,
                price_type=CurrencyKind.NONE,
                energy_cost=0,
                crystal_cost=0,
                is_expert_role=True,
                blocked=True,
                block_reason="expert_role_requires_paid_tariff",
            )
        return ChatRoutePlan(
            model_id=FREE_CHAT_MODEL,
            price_type=CurrencyKind.ENERGY,
            energy_cost=CHAT_STANDARD_ENERGY,
            crystal_cost=CHAT_STANDARD_CRYSTALS,
            is_expert_role=False,
        )

    model_id = PAID_CHAT_MODEL
    if expert:
        return ChatRoutePlan(
            model_id=model_id,
            price_type=CurrencyKind.ENERGY,
            energy_cost=CHAT_EXPERT_ENERGY,
            crystal_cost=CHAT_EXPERT_CRYSTALS,
            is_expert_role=True,
        )
    return ChatRoutePlan(
        model_id=model_id,
        price_type=CurrencyKind.ENERGY,
        energy_cost=CHAT_STANDARD_ENERGY,
        crystal_cost=CHAT_STANDARD_CRYSTALS,
        is_expert_role=False,
    )


def can_afford_chat(user: UserBillingState, plan: ChatRoutePlan) -> bool:
    if plan.blocked:
        return False
    if user.total_energy >= plan.energy_cost:
        return True
    return user.crystals >= plan.crystal_cost


async def handle_text_chat(user_id: int, role_type: str) -> tuple[ChatRoutePlan, str | None]:
    """
    План + атомарное списание.

    Returns:
        (plan, charge_id | None) — charge_id для отката при ошибке API.
    """
    user = await store.load_user_billing(user_id)
    plan = plan_text_chat(user, role_type)
    if plan.blocked:
        return plan, None
    if not can_afford_chat(user, plan):
        if plan.is_expert_role:
            return ChatRoutePlan(
                model_id=plan.model_id,
                price_type=CurrencyKind.NONE,
                energy_cost=plan.energy_cost,
                crystal_cost=plan.crystal_cost,
                is_expert_role=True,
                blocked=True,
                block_reason="insufficient_for_expert",
            ), None
        return plan, None

    user = await store.load_user_billing(user_id)
    energy_need = plan.energy_cost
    crystal_need = plan.crystal_cost
    if user.total_energy >= energy_need:
        crystal_need = 0
    else:
        energy_need = 0

    charge = await store.atomic_spend(
        user_id,
        SpendFeature.CHAT.value,
        energy_need=energy_need,
        crystal_need=crystal_need,
        crystals_only=False,
        reserve_photo_slot=False,
        photo_daily_limit=0,
    )
    if not charge:
        return ChatRoutePlan(
            model_id=plan.model_id,
            price_type=CurrencyKind.NONE,
            energy_cost=plan.energy_cost,
            crystal_cost=plan.crystal_cost,
            is_expert_role=plan.is_expert_role,
            blocked=True,
            block_reason="spend_failed",
        ), None

    price_type = CurrencyKind.ENERGY if charge.energy_free or charge.energy_paid else CurrencyKind.CRYSTALS
    return ChatRoutePlan(
        model_id=plan.model_id,
        price_type=price_type,
        energy_cost=charge.energy_free + charge.energy_paid,
        crystal_cost=charge.crystals,
        is_expert_role=plan.is_expert_role,
    ), charge.charge_id
