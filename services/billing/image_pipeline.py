"""Матрица цен и списание за генерацию изображений."""

from __future__ import annotations

from services.billing import store
from services.billing.pricing import (
    FREE_IMAGEN_DAILY_LIMIT,
    FREE_IMAGEN_OVERLIMIT_COST,
    FREE_PRO_IMAGE_COST,
    IMAGE_MODEL_ALIASES,
    PAID_IMAGE_MATRIX,
)
from services.billing.types import ImageSpendPlan, SpendFeature, SpendResult, TariffTier

FREE_TIER_IMAGE_MODELS = frozenset({"imagen4", "flux_schnell"})


def normalize_image_model(model_name: str) -> str:
    raw = (model_name or "").strip().lower().replace("-", "_")
    return IMAGE_MODEL_ALIASES.get(raw, raw)


def build_image_spend_plan(
    tariff: TariffTier,
    model_key: str,
    *,
    daily_count: int,
    daily_date: str | None,
) -> ImageSpendPlan:
    from datetime import date

    today = date.today().isoformat()
    count = daily_count if daily_date == today else 0

    if tariff is TariffTier.FREE:
        if model_key not in FREE_TIER_IMAGE_MODELS:
            return ImageSpendPlan(
                model_key=model_key,
                energy_cost=0,
                crystal_cost=0,
                crystals_only=True,
                use_free_daily_slot=False,
                blocked=True,
                block_reason="free_image_model_blocked",
            )
        if model_key == "imagen4":
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
                crystal_cost=FREE_IMAGEN_OVERLIMIT_COST,
                crystals_only=True,
                use_free_daily_slot=False,
            )
        if model_key == "flux_schnell":
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
                crystal_cost=FREE_PRO_IMAGE_COST,
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
    """Атомарно списать ресурсы за фото. Возвращает charge_id для отката."""
    user = await store.load_user_billing(user_id)
    model_key = normalize_image_model(model_name)
    plan = build_image_spend_plan(
        user.current_tariff,
        model_key,
        daily_count=user.photo_daily_count,
        daily_date=user.photo_daily_date,
    )

    if plan.blocked:
        return SpendResult(ok=False, error=plan.block_reason or "free_image_model_blocked")

    energy_need = 0
    crystal_need = plan.crystal_cost
    if plan.use_free_daily_slot:
        charge = await store.atomic_spend(
            user_id,
            SpendFeature.IMAGE.value,
            energy_need=0,
            crystal_need=0,
            crystals_only=False,
            reserve_photo_slot=True,
            photo_daily_limit=FREE_IMAGEN_DAILY_LIMIT,
        )
        if charge:
            return SpendResult(ok=True, charge=charge)
        # Слот занят (гонка или лимит) — докупка Imagen 4 за кристаллы на FREE.
        if user.current_tariff is TariffTier.FREE and model_key == "imagen4":
            charge = await store.atomic_spend(
                user_id,
                SpendFeature.IMAGE.value,
                energy_need=0,
                crystal_need=FREE_IMAGEN_OVERLIMIT_COST,
                crystals_only=True,
                reserve_photo_slot=False,
                photo_daily_limit=FREE_IMAGEN_DAILY_LIMIT,
            )
            if charge:
                return SpendResult(ok=True, charge=charge)
            return SpendResult(ok=False, error="insufficient_balance")
        if user.current_tariff is TariffTier.FREE and model_key == "flux_schnell":
            charge = await store.atomic_spend(
                user_id,
                SpendFeature.IMAGE.value,
                energy_need=0,
                crystal_need=FREE_PRO_IMAGE_COST,
                crystals_only=True,
                reserve_photo_slot=False,
                photo_daily_limit=FREE_IMAGEN_DAILY_LIMIT,
            )
            if charge:
                return SpendResult(ok=True, charge=charge)
            return SpendResult(ok=False, error="insufficient_balance")
        return SpendResult(ok=False, error="daily_limit_exceeded")

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
