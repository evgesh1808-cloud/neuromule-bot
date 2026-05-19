"""HD / Gemini сервисы (совет, разбор, совместимость)."""

from __future__ import annotations

from services.billing import store
from services.billing.pricing import (
    ANIMATE_COST,
    HD_ADVICE_COST,
    HD_FULL_REPORT_COST,
    HD_MATCH_COST,
    MUSIC_COST,
    UPSCALE_COST,
)
from services.billing.types import SpendFeature, SpendResult


async def spend_hd_advice(user_id: int) -> SpendResult:
    """Совет дня — 0 💎 (лимит 1/день снаружи)."""
    if HD_ADVICE_COST == 0:
        return SpendResult(ok=True, charge=None)
    return await _spend_crystals_only(user_id, HD_ADVICE_COST, SpendFeature.HD_ADVICE)


async def spend_hd_full_report(user_id: int) -> SpendResult:
    return await _spend_crystals_only(user_id, HD_FULL_REPORT_COST, SpendFeature.HD_REPORT)


async def spend_hd_match(user_id: int) -> SpendResult:
    return await _spend_crystals_only(user_id, HD_MATCH_COST, SpendFeature.HD_MATCH)


async def spend_upscale(user_id: int) -> SpendResult:
    return await _spend_crystals_only(user_id, UPSCALE_COST, SpendFeature.UPSCALE)


async def spend_animate(user_id: int) -> SpendResult:
    return await _spend_crystals_only(user_id, ANIMATE_COST, SpendFeature.ANIMATE)


async def spend_music(user_id: int) -> SpendResult:
    return await _spend_crystals_only(user_id, MUSIC_COST, SpendFeature.MUSIC)


async def _spend_crystals_only(user_id: int, amount: int, feature: SpendFeature) -> SpendResult:
    charge = await store.atomic_spend(
        user_id,
        feature.value,
        energy_need=0,
        crystal_need=amount,
        crystals_only=True,
        reserve_photo_slot=False,
        photo_daily_limit=0,
    )
    if not charge:
        return SpendResult(ok=False, error="insufficient_crystals")
    return SpendResult(ok=True, charge=charge)
