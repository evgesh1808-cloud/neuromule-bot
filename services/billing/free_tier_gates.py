"""Ограничения тарифа FREE для раздела «🎨 Создать»."""

from __future__ import annotations

from services.billing import store
from services.billing.daily_quotas import (
    QuotaSnapshot,
    get_free_photo_snapshot,
    get_global_free_image_snapshot,
    quota_day,
)
from services.billing.image_pipeline import free_allows_model_key, normalize_image_model
from services.billing.types import TariffTier


async def get_user_tariff(user_id: int) -> TariffTier:
    user = await store.load_user_billing(user_id)
    return user.current_tariff


def is_free_tariff(tariff: TariffTier) -> bool:
    return tariff is TariffTier.FREE


async def is_free_user(user_id: int) -> bool:
    return is_free_tariff(await get_user_tariff(user_id))


def free_allows_image_model(model_id: str) -> bool:
    return False


async def free_photo_quota_snapshot(user_id: int) -> QuotaSnapshot:
    """Снимок user_daily_quotas (free_banana_daily) для Nano Banana FREE."""
    return await get_free_photo_snapshot(user_id, day=quota_day())


async def get_remaining_global_banana_slots() -> int:
    """Скрытый остаток GLOBAL_FREE_IMAGE_DAILY_CAP (не для UI)."""
    from services.billing.daily_quotas import get_remaining_global_banana_slots as _rem

    return await _rem()


async def global_free_image_cap_exhausted() -> bool:
    """True — суточный GLOBAL_FREE_IMAGE_DAILY_CAP исчерпан."""
    snap = await get_global_free_image_snapshot(day=quota_day())
    return snap.exhausted


async def free_photo_slot_available(user_id: int) -> bool:
    """True — доступен бесплатный слот Nano Banana (user + global cap)."""
    today = quota_day()
    user_snap = await get_free_photo_snapshot(user_id, day=today)
    if user_snap.exhausted:
        return False
    global_snap = await get_global_free_image_snapshot(day=today)
    return not global_snap.exhausted


async def free_blocks_premium_create(user_id: int) -> bool:
    """True — нужно показать экран блокировки (изображение, анимация, музыка, видео, HD)."""
    from services.god_mode import billing_bypass

    if billing_bypass(user_id):
        return False
    return await is_free_user(user_id)
