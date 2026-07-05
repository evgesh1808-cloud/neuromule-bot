"""Списание за мультиформатную адаптацию поста блогера."""

from __future__ import annotations

from services.billing import store
from services.billing.pricing_constants import BLOGGER_ADAPT_COST
from services.billing.types import SpendFeature, SpendResult


async def spend_blogger_adapt(user_id: int) -> SpendResult:
    """Reels / VC.ru / Twitter — 3 💎 за один реформат."""
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
