"""Обработка покупок в магазине."""

from __future__ import annotations

import logging

from services.billing import store
from services.billing.pricing import REFERRAL_FIRST_PURCHASE_CRYSTALS, SHOP_PACKS
from services.billing.types import PurchaseResult, ShopPackName, TariffTier

logger = logging.getLogger(__name__)

_PACK_ALIASES: dict[str, str] = {
    "mini": ShopPackName.MINI.value,
    "smart": ShopPackName.SMART.value,
    "ultra": ShopPackName.ULTRA.value,
    "10": ShopPackName.CRYSTALS_10.value,
    "40": ShopPackName.CRYSTALS_40.value,
    "100": ShopPackName.CRYSTALS_100.value,
}


def normalize_pack_name(pack_name: str) -> str | None:
    raw = (pack_name or "").strip()
    if raw in SHOP_PACKS:
        return raw
    key = raw.lower().replace(" ", "_")
    if key in _PACK_ALIASES:
        return _PACK_ALIASES[key]
    if key.startswith("crystals_"):
        return key
    return None


async def process_purchase(user_id: int, pack_name: str) -> PurchaseResult:
    """
    Начисляет ресурсы по пакету, обновляет тариф (для MINI/SMART/ULTRA),
    обрабатывает реферальный бонус при первой покупке.
    """
    normalized = normalize_pack_name(pack_name)
    if not normalized or normalized not in SHOP_PACKS:
        return PurchaseResult(ok=False, pack_name=pack_name, error="unknown_pack")

    spec = SHOP_PACKS[normalized]
    energy_add = int(spec["energy_paid"])
    crystals_add = int(spec["crystals"])
    tariff_raw = spec.get("tariff")

    await store.init_billing_schema()
    inviter = await store.mark_first_purchase_done(user_id)
    referral_bonus = 0
    if inviter and inviter > 0:
        referral_bonus = REFERRAL_FIRST_PURCHASE_CRYSTALS
        await store.apply_purchase_credits(
            inviter,
            tariff=None,
            energy_paid_delta=0,
            crystals_delta=referral_bonus,
        )

    await store.apply_purchase_credits(
        user_id,
        tariff=str(tariff_raw) if tariff_raw else None,
        energy_paid_delta=energy_add,
        crystals_delta=crystals_add,
    )

    tariff_updated = TariffTier.from_db(str(tariff_raw)) if tariff_raw else None
    logger.info(
        "purchase user_id=%s pack=%s energy+%s crystals+%s tariff=%s referral_to=%s",
        user_id,
        normalized,
        energy_add,
        crystals_add,
        tariff_raw,
        inviter,
    )
    return PurchaseResult(
        ok=True,
        pack_name=normalized,
        tariff_updated=tariff_updated,
        energy_paid_added=energy_add,
        crystals_added=crystals_add,
        referral_crystals_to_inviter=referral_bonus if inviter else 0,
    )


def pack_name_from_catalog_index(index: int) -> str | None:
    """Индексы из ``payments_catalog.PACKAGES``."""
    mapping = {
        0: ShopPackName.MINI.value,
        1: ShopPackName.SMART.value,
        2: ShopPackName.ULTRA.value,
        3: ShopPackName.CRYSTALS_10.value,
        4: ShopPackName.CRYSTALS_40.value,
        5: ShopPackName.CRYSTALS_100.value,
    }
    return mapping.get(index)
