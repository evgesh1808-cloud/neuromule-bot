"""Меню PRO-видео и пранков (callback id → billing scenario)."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from content import messages as msg
from services.billing.pricing import VIDEO_EXTEND_5SEC, VIDEO_LONG_15_20, VIDEO_PRO_5SEC
from services.billing.video_pipeline import VIDEO_SCENARIOS

CB_VIDEO_PREFIX = "vid:"
CB_VIDEO_CAT_PREFIX = "vidcat:"
CB_VIDEO_EXTEND = "vid:extend"
CB_VIDEO_LONG = "vid:long"
CB_VIDEO_CUSTOM_TEXT = "vid:custom_text_only"
CB_VIDEO_CUSTOM_PHOTO = "vid:custom_photo_script"
CB_VIDEO_CUSTOM_VIDEO = "vid:custom_video_script"
CB_VIDEO_PRO_5 = "vid:video_pro_5sec"

VIDEO_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("😅 Бытовые боли (50–70 💎)", "pain"),
    ("🎭 Пранки с лицом (70–100 💎)", "face"),
    ("✍️ Свой сценарий (40–80 💎)", "custom"),
    ("🎬 PRO 5 сек (35 💎)", "pro"),
)


def _scenario_button(spec_id: str, title: str, cost: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=f"{title} — {cost} 💎",
        callback_data=f"{CB_VIDEO_PREFIX}{spec_id}",
    )


def video_root_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"{CB_VIDEO_CAT_PREFIX}{cat}")]
        for label, cat in VIDEO_CATEGORIES
    ]
    rows.append([InlineKeyboardButton(text=msg.TXT_BACK_TO_TOOLS, callback_data=msg.CB_BACK_CREATE)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def video_category_menu(category: str) -> InlineKeyboardMarkup:
    items: list[InlineKeyboardButton] = []
    for spec in VIDEO_SCENARIOS.values():
        if category == "pain" and spec.category.startswith("pain"):
            items.append(_scenario_button(spec.scenario_id, spec.title_ru, spec.crystal_cost))
        elif category == "face" and spec.category.startswith("face"):
            items.append(_scenario_button(spec.scenario_id, spec.title_ru, spec.crystal_cost))
        elif category == "custom" and spec.category == "custom":
            items.append(_scenario_button(spec.scenario_id, spec.title_ru, spec.crystal_cost))
        elif category == "pro" and spec.scenario_id == "video_pro_5sec":
            items.append(_scenario_button(spec.scenario_id, spec.title_ru, spec.crystal_cost))
    rows = [[btn] for btn in items[:12]]
    if len(items) > 12:
        rows.append([btn for btn in items[12:24]])
    rows.append([InlineKeyboardButton(text="⬅️ К видео-меню", callback_data=msg.CB_CREATE_VIDEO)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def result_video_keyboard_pro() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⏱ Продлить видео (+5 сек) — {VIDEO_EXTEND_5SEC} 💎",
                    callback_data=CB_VIDEO_EXTEND,
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🎞 Длинное PRO (15–20 сек) — {VIDEO_LONG_15_20} 💎",
                    callback_data=CB_VIDEO_LONG,
                )
            ],
            [InlineKeyboardButton(text="🚀 Тарифы", callback_data=msg.CB_RESULT_PREMIUM)],
        ]
    )
