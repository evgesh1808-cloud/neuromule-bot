"""Прайс-лист Billing Manager — реэкспорт из ``business_catalog`` (единый источник правды)."""

from __future__ import annotations

from business_catalog import (
    FREE_CHAT_MODEL,
    IMAGE_MODEL_ALIASES,
    PAID_CHAT_MODEL,
    catalog,
)

# --- Текстовый чат ---
CHAT_STANDARD_ENERGY = catalog.chat.standard_energy
CHAT_STANDARD_CRYSTALS = catalog.chat.standard_crystals
CHAT_EXPERT_ENERGY = catalog.chat.expert_energy
CHAT_EXPERT_CRYSTALS = catalog.chat.expert_crystals

# --- HD ---
HD_ADVICE_COST = catalog.hd_advice_cost
HD_FULL_REPORT_COST = catalog.hd_full_report_cost
HD_MATCH_COST = catalog.hd_match_cost

# --- Магазин ---
SHOP_PACKS = catalog.shop_packs

REFERRAL_FIRST_PURCHASE_CRYSTALS = catalog.referral_first_purchase_crystals
DAILY_FREE_ENERGY = catalog.daily_free_energy
FREE_IMAGEN_DAILY_LIMIT = catalog.free_imagen_daily_limit

FREE_OTHER_IMAGE_CRYSTALS = catalog.free_other_image_crystals

PAID_IMAGE_MATRIX: dict[str, tuple[tuple[int, int], bool]] = {
    key: ((m.energy, m.crystals), m.crystals_only)
    for key, m in catalog.paid_image_models.items()
}

# --- Видео PRO / тарифы сценариев ---
VIDEO_PRO_5SEC = catalog.video_tiers.pro_5sec
VIDEO_EXTEND_5SEC = catalog.video_tiers.extend_5sec
VIDEO_LONG_15_20 = catalog.video_tiers.long_15_20
VIDEO_TIER_50 = catalog.video_tiers.tier_50
VIDEO_TIER_70 = catalog.video_tiers.tier_70
VIDEO_TIER_80 = catalog.video_tiers.tier_80
VIDEO_TIER_100 = catalog.video_tiers.tier_100

MUSIC_COST = catalog.music_cost
ANIMATE_COST = catalog.animate_cost
UPSCALE_COST = catalog.upscale_cost
