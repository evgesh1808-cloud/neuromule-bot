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
from services.billing.types import SpendFeature, SpendResult, TariffTier


async def spend_hd_advice(user_id: int) -> SpendResult:
    """Совет дня — 0 💎 (лимит 1/день снаружи)."""
    if HD_ADVICE_COST == 0:
        return SpendResult(ok=True, charge=None)
    return await _spend_crystals_only(user_id, HD_ADVICE_COST, SpendFeature.HD_ADVICE)


async def spend_hd_full_report(user_id: int) -> SpendResult:
    user = await store.load_user_billing(user_id)
    if user.current_tariff is TariffTier.FREE:
        return SpendResult(ok=False, error="free_premium_create_blocked")
    return await _spend_crystals_only(user_id, HD_FULL_REPORT_COST, SpendFeature.HD_REPORT)


async def spend_hd_match(user_id: int) -> SpendResult:
    user = await store.load_user_billing(user_id)
    if user.current_tariff is TariffTier.FREE:
        return SpendResult(ok=False, error="free_premium_create_blocked")
    return await _spend_crystals_only(user_id, HD_MATCH_COST, SpendFeature.HD_MATCH)


UPSCALE_ENERGY_FALLBACK = 10  # 1 💎 == 10 ⚡ для резервной оплаты UPSCALE


async def spend_upscale(user_id: int) -> SpendResult:
    """UPSCALE: 1 💎; при 0 💎 — фоллбэк 10 ⚡."""
    user = await store.load_user_billing(user_id)
    if user.crystals >= UPSCALE_COST:
        return await _spend_crystals_only(user_id, UPSCALE_COST, SpendFeature.UPSCALE)
    charge = await store.atomic_spend(
        user_id,
        SpendFeature.UPSCALE.value,
        energy_need=UPSCALE_ENERGY_FALLBACK,
        crystal_need=0,
        crystals_only=False,
        reserve_photo_slot=False,
        photo_daily_limit=0,
    )
    if not charge:
        return SpendResult(ok=False, error="insufficient_balance")
    return SpendResult(ok=True, charge=charge)


async def spend_animate(user_id: int) -> SpendResult:
    user = await store.load_user_billing(user_id)
    if user.current_tariff is TariffTier.FREE:
        return SpendResult(ok=False, error="free_premium_create_blocked")
    return await _spend_crystals_only(user_id, ANIMATE_COST, SpendFeature.ANIMATE)


async def spend_music(user_id: int) -> SpendResult:
    """Музыка Suno: 15 💎. Доступна для всех платных тарифов (MINI/SMART/ULTRA)."""
    user = await store.load_user_billing(user_id)
    if user.current_tariff is TariffTier.FREE:
        return SpendResult(ok=False, error="free_premium_create_blocked")
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
