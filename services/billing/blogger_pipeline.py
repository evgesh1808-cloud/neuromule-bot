"""Списание за функции конструктора «Блогер» (адаптация / AI-обложка)."""

from __future__ import annotations

from services.billing import store
from services.billing.pricing_constants import BLOGGER_ADAPT_COST, BLOGGER_COVER_COST
from services.billing.types import SpendFeature, SpendResult
from services.god_mode import billing_bypass


async def _total_crystals(user_id: int) -> int:
    user = await store.load_user_billing(user_id)
    return int(user.crystals or 0)


async def can_afford_blogger_adapt(user_id: int) -> bool:
    if billing_bypass(user_id):
        return True
    return await _total_crystals(user_id) >= BLOGGER_ADAPT_COST


async def can_afford_blogger_cover(user_id: int) -> bool:
    if billing_bypass(user_id):
        return True
    return await _total_crystals(user_id) >= BLOGGER_COVER_COST


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
    """AI-обложка поста — 4 💎 (кристаллы, в т.ч. на тарифе FREE)."""
    charge = await store.atomic_spend(
        user_id,
        SpendFeature.BLOGGER_COVER.value,
        energy_need=0,
        crystal_need=BLOGGER_COVER_COST,
        crystals_only=True,
        reserve_photo_slot=False,
        photo_daily_limit=0,
    )
    if not charge:
        return SpendResult(ok=False, error="insufficient_crystals")
    return SpendResult(ok=True, charge=charge)
