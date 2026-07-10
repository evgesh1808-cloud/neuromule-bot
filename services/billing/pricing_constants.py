"""
Именованные константы тарифной сетки (единый источник — ``business_catalog.catalog``).

Значения по умолчанию заданы в ``config.Settings`` и могут переопределяться через ``.env``.
"""

from __future__ import annotations

from business_catalog import catalog

# --- Текстовый чат ---
FREE_CHAT_ENERGY_COST = catalog.chat.standard_energy
FREE_CHAT_CRYSTAL_COST = catalog.chat.standard_crystals
PAID_CHAT_EXPERT_ENERGY_COST = catalog.chat.expert_energy
PAID_CHAT_EXPERT_CRYSTAL_COST = catalog.chat.expert_crystals

# --- Изображения ---
FREE_DAILY_IMAGEN_LIMIT = catalog.free_imagen_daily_limit
FREE_IMAGEN_OVERLIMIT_COST = catalog.free_imagen_overlimit_cost
FREE_PRO_IMAGE_COST = catalog.free_pro_image_cost

_paid = catalog.paid_image_models
PAID_IMAGEN_ENERGY_COST = _paid["imagen4"].energy
PAID_IMAGEN_CRYSTAL_COST = _paid["imagen4"].crystals
PAID_FLUX_ENERGY_COST = _paid["flux_schnell"].energy
PAID_FLUX_CRYSTAL_COST = _paid["flux_schnell"].crystals
PAID_BANANA2_ENERGY_COST = _paid["nano_banana2"].energy
PAID_BANANA2_CRYSTAL_COST = _paid["nano_banana2"].crystals
PAID_BANANA_PRO_ENERGY_COST = _paid["nano_banana_pro"].energy
PAID_BANANA_PRO_CRYSTAL_COST = _paid["nano_banana_pro"].crystals
GLOBAL_GPT_IMAGE2_COST = _paid["gpt_image2"].crystals

# --- Видео / музыка ---
_v = catalog.video_tiers
COST_SUNO_MUSIC = catalog.music_cost
COST_TIER_50 = _v.tier_50
COST_TIER_70 = _v.tier_70
COST_TIER_80 = _v.tier_80
COST_TIER_100 = _v.tier_100
COST_PRO_5SEC = _v.pro_5sec
COST_EXTEND_5SEC = _v.extend_5sec
COST_LONG_15_20 = _v.long_15_20
COST_CUSTOM_TEXT = _v.custom_text
COST_CUSTOM_PHOTO = _v.custom_photo
COST_CUSTOM_VIDEO = _v.custom_video

# --- Human Design ---
COST_HD_FULL = catalog.hd_full_report_cost
COST_HD_MATCH = catalog.hd_match_cost

# --- Блогер: мультиформатная адаптация и AI-обложка ---
BLOGGER_ADAPT_COST = 3
BLOGGER_COVER_COST = 4
