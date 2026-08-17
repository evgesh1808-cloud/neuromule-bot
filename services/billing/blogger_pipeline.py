"""Списание за функции конструктора «Блогер» (адаптация / AI-обложка)."""

from __future__ import annotations

from services.billing import store
from services.billing.image_pipeline import (
    build_image_spend_plan,
    free_tier_image_model,
    spend_image_resource,
)
from services.billing.pricing_constants import BLOGGER_ADAPT_COST
from services.billing.types import SpendFeature, SpendResult, TariffTier
from services.god_mode import billing_bypass

from business_catalog import FLUX_2_PRO_MODEL_KEY

BLOGGER_COVER_IMAGE_MODEL = FLUX_2_PRO_MODEL_KEY


def _cover_model_for_tariff(tariff: TariffTier) -> str:
    """FREE — общий free_photo слот; платные тарифы — Flux Schnell."""
    if tariff is TariffTier.FREE:
        return free_tier_image_model()
    return BLOGGER_COVER_IMAGE_MODEL


async def _total_crystals(user_id: int) -> int:
    user = await store.load_user_billing(user_id)
    return int(user.crystals or 0)


async def can_afford_blogger_adapt(user_id: int) -> bool:
    if billing_bypass(user_id):
        return True
    return await _total_crystals(user_id) >= BLOGGER_ADAPT_COST


async def can_afford_blogger_cover(user_id: int) -> bool:
    """Обложка блогера: FREE — free_photo (1/день), платные — Flux Schnell."""
    if billing_bypass(user_id):
        return True
    user = await store.load_user_billing(user_id)
    model = _cover_model_for_tariff(user.current_tariff)
    plan = build_image_spend_plan(
        user.current_tariff,
        model,
        daily_count=user.photo_daily_count,
        daily_date=user.photo_daily_date,
    )
    if plan.blocked:
        return False
    if plan.use_free_daily_slot:
        return True
    if plan.crystals_only:
        return user.crystals >= plan.crystal_cost
    if user.total_energy >= plan.energy_cost:
        return True
    return user.crystals >= plan.crystal_cost


async def spend_blogger_adapt(user_id: int) -> SpendResult:
    """VK / VC.ru / МАКС / Видео — 3 💎 за один реформат."""
    charge = await store.atomic_spend(
        user_id,
        SpendFeature.BLOGGER_ADAPT.value,
        energy_need=0,
        crystal_need=BLOGGER_ADAPT_COST,
        crystals_only=True,
        reserve_photo_slot=False,
        photo_daily_limit=0,
    )
    if not charge:
        return SpendResult(ok=False, error="insufficient_crystals")
    return SpendResult(ok=True, charge=charge)


async def spend_blogger_cover(user_id: int) -> SpendResult:
    """AI-обложка: FREE — free_photo (общая квота), платные — Flux Schnell."""
    user = await store.load_user_billing(user_id)
    model = _cover_model_for_tariff(user.current_tariff)
    return await spend_image_resource(user_id, model)
