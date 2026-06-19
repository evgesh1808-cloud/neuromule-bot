"""Ограничения тарифа FREE для раздела «🎨 Создать»."""

from __future__ import annotations

from services.billing import store
from services.billing.image_pipeline import normalize_image_model
from services.billing.types import TariffTier

FREE_CREATE_IMAGE_MODELS = frozenset({"imagen4", "flux_schnell", "flux-schnell"})


async def get_user_tariff(user_id: int) -> TariffTier:
    user = await store.load_user_billing(user_id)
    return user.current_tariff


def is_free_tariff(tariff: TariffTier) -> bool:
    return tariff is TariffTier.FREE


async def is_free_user(user_id: int) -> bool:
    return is_free_tariff(await get_user_tariff(user_id))


def free_allows_image_model(model_id: str) -> bool:
    key = normalize_image_model(model_id)
    return key in {"imagen4", "flux_schnell"}


async def free_blocks_premium_create(user_id: int) -> bool:
    """True — нужно показать экран блокировки (анимация, музыка, видео, HD)."""
    return await is_free_user(user_id)
