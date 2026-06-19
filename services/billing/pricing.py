"""Прайс-лист Billing Manager — реэкспорт из ``business_catalog`` (единый источник правды)."""

from __future__ import annotations

from business_catalog import (
    FREE_CHAT_MODEL,
    IMAGE_MODEL_ALIASES,
    PAID_CHAT_MODEL,
    catalog,
)
from services.billing import pricing_constants as pc

# --- Текстовый чат ---
CHAT_STANDARD_ENERGY = pc.FREE_CHAT_ENERGY_COST
CHAT_STANDARD_CRYSTALS = pc.FREE_CHAT_CRYSTAL_COST
CHAT_EXPERT_ENERGY = pc.PAID_CHAT_EXPERT_ENERGY_COST
CHAT_EXPERT_CRYSTALS = pc.PAID_CHAT_EXPERT_CRYSTAL_COST

# --- HD ---
HD_ADVICE_COST = catalog.hd_advice_cost
HD_FULL_REPORT_COST = pc.COST_HD_FULL
HD_MATCH_COST = pc.COST_HD_MATCH

# --- Магазин ---
# Единый источник: ``business_catalog.build_catalog()`` → ``config.settings``.
# Stars-цены заложены с наценкой к карте (паритет 2 ₽/⭐, см. ``STARS_RUB_PARITY``).
SHOP_PACKS = catalog.shop_packs

# Паритет ₽ за 1 Telegram Star для расчёта наценки в документации и UX-текстах.
STARS_RUB_PARITY: float = 2.0


def stars_markup_percent(price_rub: int, price_stars: int, *, parity: float = STARS_RUB_PARITY) -> float:
    """Наценка Stars к карте, %: (stars×parity − rub) / rub × 100."""
    if price_rub <= 0:
        return 0.0
    return round((price_stars * parity - price_rub) / price_rub * 100, 1)


def card_savings_vs_stars_percent(price_rub: int, price_stars: int, *, parity: float = STARS_RUB_PARITY) -> float:
    """Экономия при оплате картой vs Stars, % от эквивалента в ⭐."""
    stars_rub = price_stars * parity
    if stars_rub <= 0:
        return 0.0
    return round((stars_rub - price_rub) / stars_rub * 100, 1)

REFERRAL_FIRST_PURCHASE_CRYSTALS = catalog.referral_first_purchase_crystals
DAILY_FREE_ENERGY = catalog.daily_free_energy
FREE_IMAGEN_DAILY_LIMIT = pc.FREE_DAILY_IMAGEN_LIMIT
FREE_IMAGEN_OVERLIMIT_COST = pc.FREE_IMAGEN_OVERLIMIT_COST
FREE_PRO_IMAGE_COST = pc.FREE_PRO_IMAGE_COST
FREE_OTHER_IMAGE_CRYSTALS = catalog.free_other_image_crystals

PAID_IMAGE_MATRIX: dict[str, tuple[tuple[int, int], bool]] = {
    key: ((m.energy, m.crystals), m.crystals_only)
    for key, m in catalog.paid_image_models.items()
}

# --- Видео PRO / тарифы сценариев ---
VIDEO_PRO_5SEC = pc.COST_PRO_5SEC
VIDEO_EXTEND_5SEC = pc.COST_EXTEND_5SEC
VIDEO_LONG_15_20 = pc.COST_LONG_15_20
VIDEO_TIER_50 = pc.COST_TIER_50
VIDEO_TIER_70 = pc.COST_TIER_70
VIDEO_TIER_80 = pc.COST_TIER_80
VIDEO_TIER_100 = pc.COST_TIER_100

MUSIC_COST = pc.COST_SUNO_MUSIC
ANIMATE_COST = catalog.animate_cost
UPSCALE_COST = catalog.upscale_cost
