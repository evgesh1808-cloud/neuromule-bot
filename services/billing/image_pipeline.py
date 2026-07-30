"""Матрица цен и списание за генерацию изображений.

FREE Nano Banana:
  * слот — ``user_daily_quotas.free_banana_daily`` (⚡ не списывается);
  * глобальный предохранитель — ``GLOBAL_FREE_IMAGE_DAILY_CAP`` (дефолт 1500);
  * генерация — Round-Robin OpenRouter(:free) ↔ Gemini Free Tier.
"""

from __future__ import annotations

from services.billing import store
from services.billing.daily_quotas import (
    get_free_photo_snapshot,
    get_global_free_image_snapshot,
    quota_day,
)
from services.billing.pricing import (
    FREE_IMAGEN_DAILY_LIMIT,
    FREE_PRO_IMAGE_COST,
    IMAGE_MODEL_ALIASES,
    PAID_IMAGE_MATRIX,
)
from services.billing.types import ImageSpendPlan, SpendFeature, SpendResult, TariffTier

FREE_PHOTO_MODEL_KEY = "free_photo"


def normalize_image_model(model_name: str) -> str:
    raw = (model_name or "").strip().lower().replace("-", "_")
    return IMAGE_MODEL_ALIASES.get(raw, raw)


def free_tier_image_model() -> str:
    """Модель 1 бесплатного Nano Banana/день на FREE (RR OpenRouter↔Gemini)."""
    from config import settings

    return normalize_image_model(settings.free_image_model)


def free_tier_overlimit_crystal_cost(model_key: str) -> int:
    """Цена докупки после исчерпания бесплатного слота на FREE."""
    matrix = PAID_IMAGE_MATRIX.get(model_key)
    if matrix:
        (_energy, crystals), _crystals_only = matrix
        return int(crystals)
    return int(FREE_PRO_IMAGE_COST)


def free_allows_model_key(model_key: str) -> bool:
    return normalize_image_model(model_key) == free_tier_image_model()


def build_image_spend_plan(
    tariff: TariffTier,
    model_key: str,
    *,
    daily_count: int,
    daily_date: str | None,
) -> ImageSpendPlan:
    today = quota_day()
    count = daily_count if daily_date == today else 0
    model_key = normalize_image_model(model_key)
    free_model = free_tier_image_model()

    if tariff is TariffTier.FREE:
        if model_key != free_model:
            return ImageSpendPlan(
                model_key=model_key,
                energy_cost=0,
                crystal_cost=0,
                crystals_only=True,
                use_free_daily_slot=False,
                blocked=True,
                block_reason="free_image_model_blocked",
            )
        if count < FREE_IMAGEN_DAILY_LIMIT:
            return ImageSpendPlan(
                model_key=model_key,
                energy_cost=0,
                crystal_cost=0,
                crystals_only=False,
                use_free_daily_slot=True,
            )
        return ImageSpendPlan(
            model_key=model_key,
            energy_cost=0,
            crystal_cost=free_tier_overlimit_crystal_cost(model_key),
            crystals_only=True,
            use_free_daily_slot=False,
        )

    matrix = PAID_IMAGE_MATRIX.get(model_key)
    if not matrix:
        return ImageSpendPlan(
            model_key=model_key,
            energy_cost=0,
            crystal_cost=FREE_PRO_IMAGE_COST,
            crystals_only=True,
            use_free_daily_slot=False,
        )
    (energy, crystals), crystals_only = matrix
    return ImageSpendPlan(
        model_key=model_key,
        energy_cost=energy,
        crystal_cost=crystals,
        crystals_only=crystals_only,
        use_free_daily_slot=False,
    )


async def spend_image_resource(user_id: int, model_name: str) -> SpendResult:
    """Атомарно списать ресурсы за фото. FREE-слот — только user_daily_quotas (без ⚡)."""
    user = await store.load_user_billing(user_id)
    model_key = normalize_image_model(model_name)
    today = quota_day()
    snap = await get_free_photo_snapshot(user_id, day=today)
    plan = build_image_spend_plan(
        user.current_tariff,
        model_key,
        daily_count=snap.used,
        daily_date=today,
    )

    if plan.blocked:
        return SpendResult(ok=False, error=plan.block_reason or "free_image_model_blocked")

    if plan.use_free_daily_slot:
        # Мягкий предохранитель бюджета на весь бот (GLOBAL_FREE_IMAGE_DAILY_CAP).
        global_snap = await get_global_free_image_snapshot(day=today)
        if global_snap.exhausted:
            return SpendResult(ok=False, error="global_free_image_cap")
        charge = await store.atomic_consume_free_photo(user_id)
        if charge:
            return SpendResult(ok=True, charge=charge)
        # Гонка / слот уже взят / user daily исчерпан.
        global_after = await get_global_free_image_snapshot(day=today)
        if global_after.exhausted:
            return SpendResult(ok=False, error="global_free_image_cap")
        return SpendResult(ok=False, error="daily_limit_exceeded")

    energy_need = 0
    crystal_need = plan.crystal_cost
    if plan.crystals_only:
        energy_need = 0
    elif user.total_energy >= plan.energy_cost:
        energy_need = plan.energy_cost
        crystal_need = 0
    else:
        energy_need = 0
        crystal_need = plan.crystal_cost

    charge = await store.atomic_spend(
        user_id,
        SpendFeature.IMAGE.value,
        energy_need=energy_need,
        crystal_need=crystal_need,
        crystals_only=plan.crystals_only,
        reserve_photo_slot=False,
        photo_daily_limit=FREE_IMAGEN_DAILY_LIMIT,
    )
    if not charge:
        return SpendResult(ok=False, error="insufficient_balance")
    return SpendResult(ok=True, charge=charge)
