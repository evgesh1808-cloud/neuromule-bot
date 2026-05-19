"""Inline и Reply-клавиатуры Telegram."""
from __future__ import annotations

import html
import re

from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings
from content import messages as msg
from platforms.telegram_utils import _invite_switch_query, is_admin_user

_TICKET_USER_ID_RE = re.compile(r"ID:\s*(?:<code>|`)(\d+)(?:</code>|`)", re.IGNORECASE)

def get_admin_inline_keyboard() -> InlineKeyboardMarkup:
    """Инлайн-меню админ-панели."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data=msg.CB_ADMIN_STATS)],
            [
                InlineKeyboardButton(
                    text="💎 Начислить кристаллы",
                    callback_data=msg.CB_ADMIN_GIVE_CRYSTALS,
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Запустить рассылку",
                    callback_data=msg.CB_ADMIN_START_BROADCAST,
                )
            ],
        ]
    )


def main_menu(user_id: int | None = None) -> types.ReplyKeyboardMarkup:
    """Главное Reply-меню: 2×2 + Тарифы/Поддержка + админ при наличии прав."""
    rows: list[list[types.KeyboardButton]] = [
        [
            types.KeyboardButton(text=msg.BTN_DAILY_ADVICE),
            types.KeyboardButton(text=msg.BTN_PROFILE),
        ],
        [
            types.KeyboardButton(text=msg.BTN_HD_SECTION),
            types.KeyboardButton(text=msg.BTN_CREATE),
        ],
        [
            types.KeyboardButton(text=msg.BTN_TARIFFS),
            types.KeyboardButton(text=msg.BTN_SUPPORT),
        ],
    ]
    if is_admin_user(user_id):
        rows.append([types.KeyboardButton(text=msg.ADMIN_MAIN_MENU_BUTTON)])
    return types.ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def create_menu() -> InlineKeyboardMarkup:
    """Симметричная сетка 2×2×2 + строка «Назад» (канал мультимедиа — не OpenRouter)."""
    rows: list[list[InlineKeyboardButton]] = []
    grid = msg.CREATE_MENU_GRID
    for i in range(0, len(grid), 2):
        chunk = grid[i : i + 2]
        rows.append([InlineKeyboardButton(text=t, callback_data=cb) for t, cb in chunk])
    back_text, back_cb = msg.CREATE_MENU_BACK_ROW
    rows.append([InlineKeyboardButton(text=back_text, callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def image_model_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"{msg.CB_IMG_PREFIX}{mid}")]
        for label, mid in msg.IMAGE_MODELS
    ]
    rows.append(
        [InlineKeyboardButton(text=msg.TXT_BACK_TO_TOOLS, callback_data=msg.CB_BACK_CREATE)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def text_role_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"{msg.CB_TEXT_ROLE_PREFIX}{role_id}")]
        for label, role_id in msg.TEXT_ROLES
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def photo_tools_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Сгенерировать фото", callback_data=msg.CB_CREATE_IMAGE)],
            [
                InlineKeyboardButton(
                    text=f"🔍 UPSCALE фото — {settings.cost_upscale} 💎",
                    callback_data=msg.CB_UPSCALE_START,
                )
            ],
        ]
    )


def cabinet_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.BTN_TARIFFS,
                    callback_data=msg.CB_RESULT_PREMIUM,
                ),
                InlineKeyboardButton(
                    text=msg.INSTRUCTION_INLINE_BUTTON_LABEL,
                    callback_data=msg.CB_SHOW_INSTRUCTION,
                ),
            ],
            [
                InlineKeyboardButton(
                    text=msg.TXT_CABINET_INVITE_BUTTON,
                    switch_inline_query=_invite_switch_query(),
                )
            ],
            [InlineKeyboardButton(text=msg.TXT_CABINET_PROMO_BUTTON, callback_data=msg.CB_CABINET_PROMO)],
            [
                InlineKeyboardButton(
                    text=msg.TXT_CABINET_CHANNEL_PROMOS,
                    url=settings.channel_url,
                )
            ],
        ]
    )
def invite_limit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_CABINET_INVITE_BUTTON,
                    switch_inline_query=_invite_switch_query(),
                )
            ],
        ]
    )


def terms_accept_keyboard() -> InlineKeyboardMarkup:
    """Экран принятия оферты при первом /start."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📄 Публичная оферта",
                    url=settings.service_offer_url,
                ),
                InlineKeyboardButton(
                    text="🔒 Политика конфиденциальности",
                    url=settings.privacy_policy_url,
                ),
            ],
            [
                InlineKeyboardButton(
                    text=msg.TXT_TERMS_ACCEPT_BTN,
                    callback_data=msg.CB_ACCEPT_RULES,
                ),
            ],
        ]
    )


def support_faq_keyboard() -> InlineKeyboardMarkup:
    """FAQ: обратная связь + ссылки на юридические документы (Telegra.ph из settings)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_FAQ_WRITE_QUESTION_BTN,
                    callback_data=msg.CB_SUPPORT_WRITE_QUESTION,
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Публичная оферта",
                    url=settings.service_offer_url,
                ),
                InlineKeyboardButton(
                    text="🔒 Политика конфиденциальности",
                    url=settings.privacy_policy_url,
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💳 Правила подписки",
                    url=settings.subscription_terms_url,
                ),
            ],
        ]
    )
def channel_subscribe_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_SUBSCRIPTION_CHANNEL_BUTTON,
                    url=settings.channel_url,
                )
            ],
        ]
    )


def channel_gate_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_CHANNEL_GATE_SUBSCRIBE_BTN,
                    url=settings.channel_url,
                ),
                InlineKeyboardButton(
                    text=msg.TXT_CHANNEL_GATE_CHECK_BTN,
                    callback_data=msg.CB_CHECK_SUBSCRIPTION,
                ),
            ],
        ]
    )


def start_welcome_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=msg.TXT_HD_WELCOME_INLINE_BODIGRAPH, callback_data=msg.CB_HD_PREMIUM_BUY)],
        ]
    )


def hd_report_sections_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=msg.TXT_HD_BTN_REPORT_MONEY, callback_data=msg.CB_HD_REPORT_MONEY)],
            [InlineKeyboardButton(text=msg.TXT_HD_BTN_REPORT_LOVE, callback_data=msg.CB_HD_REPORT_LOVE)],
            [InlineKeyboardButton(text=msg.TXT_HD_BTN_REPORT_ENERGY, callback_data=msg.CB_HD_REPORT_ENERGY)],
            [InlineKeyboardButton(text=msg.TXT_HD_BTN_REPORT_PLAN, callback_data=msg.CB_HD_REPORT_PLAN)],
            [InlineKeyboardButton(text=msg.TXT_HD_BTN_REPORT_PDF, callback_data=msg.CB_HD_REPORT_PDF)],
        ]
    )


def hd_menu(has_pro: bool = False) -> InlineKeyboardMarkup:
    """Меню Дизайна человека: без покупки — только полный разбор; после покупки — просмотр + совместимость."""
    rows: list[list[InlineKeyboardButton]] = []
    if has_pro:
        rows.append(
            [
                InlineKeyboardButton(text=msg.TXT_HD_INLINE_VIEW_REPORT, callback_data=msg.CB_HD_REPORT_OPEN),
                InlineKeyboardButton(text=msg.TXT_HD_INLINE_COMPATIBILITY, callback_data=msg.CB_MATCH_START),
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text=msg.TXT_HD_INLINE_FULL_REPORT.format(cost=settings.cost_hd),
                    callback_data=msg.CB_HD_PREMIUM_BUY,
                ),
            ]
        )
    rows.append([InlineKeyboardButton(text=msg.TXT_HD_BACK_TO_TOOLS, callback_data=msg.CB_BACK_CREATE)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def hd_pro_unlocked_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=msg.TXT_HD_INLINE_COMPATIBILITY, callback_data=msg.CB_MATCH_START)],
        ]
    )


def service_rules_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📜 Публичная оферта", url=settings.service_offer_url)],
            [InlineKeyboardButton(text="🔒 Политика конфиденциальности", url=settings.privacy_policy_url)],
            [InlineKeyboardButton(text="🔁 Условия подписки", url=settings.subscription_terms_url)],
            [InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data=msg.CB_BACK_MAIN)],
        ]
    )
