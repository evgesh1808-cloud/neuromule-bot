"""Inline и Reply-клавиатуры Telegram."""
from __future__ import annotations

import html
import re
from datetime import date

from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

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


def main_menu(
    user_id: int | None = None,
    *,
    is_admin: bool | None = None,
) -> types.ReplyKeyboardMarkup:
    """Главное Reply-меню после активации.

    Эргономичная сетка 3×(1+2+2):
        • Ряд 1 — «🎨 Создать» на весь ряд (главный CTA).
        • Ряд 2 — «🔮 Совет дня» ┃ «👤 Мой профиль».
        • Ряд 3 — «🚀 Тарифы»   ┃ «🆘 Поддержка».
        • Ряд 4 (опционально)   — «⚙️ Админ-панель» только для админов.

    Параметры:
        user_id:  Telegram ID пользователя. Если ``is_admin`` не задан, статус
                  админа вычисляется автоматически через ``is_admin_user``.
        is_admin: Опциональный явный флаг админа. Когда вызывающий код уже
                  знает результат (например, ``user_id in tuple(settings.admin_ids)``),
                  можно прокинуть его сюда — экономит лишнюю выборку из конфига
                  и делает контракт хендлера явным.
    """
    resolved_is_admin = (
        bool(is_admin) if is_admin is not None else is_admin_user(user_id)
    )
    rows: list[list[types.KeyboardButton]] = [
        [types.KeyboardButton(text=msg.BTN_CREATE)],
        [
            types.KeyboardButton(text=msg.BTN_DAILY_ADVICE),
            types.KeyboardButton(text=msg.BTN_PROFILE),
        ],
        [
            types.KeyboardButton(text=msg.BTN_TARIFFS),
            types.KeyboardButton(text=msg.BTN_SUPPORT),
        ],
    ]
    if resolved_is_admin:
        rows.append([types.KeyboardButton(text=msg.ADMIN_MAIN_MENU_BUTTON)])
    return types.ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Выбери действие ниже 👇",
    )


def create_reply_menu() -> types.ReplyKeyboardMarkup:
    """Reply-подменю инструментов (кнопка «🎨 Создать»)."""
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text=msg.BTN_REPLY_NEUROTEXT)],
            [types.KeyboardButton(text=msg.BTN_REPLY_IMAGE)],
            [types.KeyboardButton(text=msg.BTN_REPLY_ANIMATE)],
            [types.KeyboardButton(text=msg.BTN_REPLY_MUSIC)],
            [types.KeyboardButton(text=msg.BTN_REPLY_VIDEO)],
            [types.KeyboardButton(text=msg.BTN_REPLY_HD)],
        ],
        resize_keyboard=True,
    )


def create_menu() -> InlineKeyboardMarkup:
    """Inline-меню «🎨 Создать».

    Гибрид:

    * Если ``settings.is_webapp_enabled is True`` И ``settings.webapp_shop_url``
      задан — отдаём ОДНУ широкую WebApp-кнопку «🚀 ОТКРЫТЬ ИИ-ПАНЕЛЬ»: вся
      фабрика инструментов живёт в Mini App, а текстовая сетка прячется.
    * Иначе — симметричная сетка 2×3 из ``CREATE_MENU_GRID`` (тексты и
      ``callback_data`` только из констант ``content.messages``) плюс
      «⬅️ Назад в главное меню``. Без URL / при выключенном флаге бот не
      падает на ``WebAppInfo``.
    """
    from aiogram.types import WebAppInfo

    webapp_url = (settings.webapp_shop_url or "").strip()
    if settings.is_webapp_enabled and webapp_url:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🚀 ОТКРЫТЬ ИИ-ПАНЕЛЬ",
                        web_app=WebAppInfo(url=webapp_url),
                    )
                ],
            ]
        )

    builder = InlineKeyboardBuilder()
    for label, callback_data in msg.CREATE_MENU_GRID:
        builder.button(text=label, callback_data=callback_data)
    # Три ряда по две кнопки — порядок пар задаётся CREATE_MENU_GRID.
    builder.adjust(2, 2, 2)

    back_text, back_cb = msg.CREATE_MENU_BACK_ROW
    builder.row(
        InlineKeyboardButton(text=back_text, callback_data=back_cb),
    )
    return builder.as_markup()


def _imagen_free_slots_left(
    photo_daily_count: int,
    photo_daily_date: str | None,
) -> int:
    today = date.today().isoformat()
    count = int(photo_daily_count or 0) if photo_daily_date == today else 0
    return max(0, settings.free_daily_photo_limit - count)


def image_model_menu(
    tariff,
    *,
    photo_daily_count: int = 0,
    photo_daily_date: str | None = None,
) -> InlineKeyboardMarkup:
    from services.billing.types import TariffTier

    if not isinstance(tariff, TariffTier):
        tariff = TariffTier.from_db(str(tariff))

    prefix = msg.CB_IMG_PREFIX
    back = InlineKeyboardButton(text="⬅️ Назад", callback_data=msg.CB_BACK_CREATE)

    if tariff is TariffTier.FREE:
        left = _imagen_free_slots_left(photo_daily_count, photo_daily_date)
        rows = [
            [InlineKeyboardButton(text=f"🎨 Imagen 4 (Осталось: {left})", callback_data=f"{prefix}imagen4")],
            [InlineKeyboardButton(text="⚡ Flux Schnell (3 💎)", callback_data=f"{prefix}flux-schnell")],
            [InlineKeyboardButton(text="🔒 DALL-E 3 (Premium)", callback_data=f"{prefix}gpt_image2")],
            [InlineKeyboardButton(text="🔒 Nano Banana 2 (Premium)", callback_data=f"{prefix}nano_banana2")],
            [InlineKeyboardButton(text="🔒 Nano Banana Pro (Premium)", callback_data=f"{prefix}nano_banana_pro")],
            [back],
        ]
    else:
        rows = [
            [InlineKeyboardButton(text="🎨 Imagen 4 (10 ⚡)", callback_data=f"{prefix}imagen4")],
            [InlineKeyboardButton(text="⚡ Flux Schnell (30 ⚡ / 3 💎)", callback_data=f"{prefix}flux-schnell")],
            [InlineKeyboardButton(text="🖼 DALL-E 3 (5 💎)", callback_data=f"{prefix}gpt_image2")],
            [InlineKeyboardButton(text="🍌 Nano Banana 2 (15 ⚡)", callback_data=f"{prefix}nano_banana2")],
            [InlineKeyboardButton(text="🚀 Nano Banana Pro (35 ⚡)", callback_data=f"{prefix}nano_banana_pro")],
            [back],
        ]

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


def _tariffs_topup_button() -> InlineKeyboardButton:
    """Кнопка «🚀 Пополнить баланс / Тарифы» в кабинете.

    Гибрид с двумя условиями:

    * ``settings.is_webapp_enabled is True`` И ``settings.webapp_shop_url``
      задан → кнопка инициализируется как WebApp (открывает Mini App
      магазина тарифов).
    * Иначе → обычный ``callback_data=CB_OPEN_TARIFFS``, бот рисует
      встроенный inline-экран тарифов как раньше.

    Безопасный rollout: без URL или при выключенном флаге бот не падает на
    ``WebAppInfo``, а просто остаётся на текстовой UX-ветке.
    """
    from aiogram.types import WebAppInfo

    url = (settings.webapp_shop_url or "").strip()
    if settings.is_webapp_enabled and url:
        return InlineKeyboardButton(
            text=msg.TXT_PROFILE_TARIFFS_BUTTON,
            web_app=WebAppInfo(url=url),
        )
    return InlineKeyboardButton(
        text=msg.TXT_PROFILE_TARIFFS_BUTTON,
        callback_data=msg.CB_OPEN_TARIFFS,
    )


def cabinet_keyboard(is_duo_owner: bool = False, *, is_ultra_owner: bool | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=msg.TXT_PROFILE_REFRESH_BUTTON,
                callback_data=msg.CB_REFRESH_PROFILE,
            )
        ],
        [_tariffs_topup_button()],
        [
            InlineKeyboardButton(
                text=msg.TXT_PROFILE_PROMO_BUTTON,
                callback_data=msg.CB_ENTER_PROMOCODE,
            )
        ],
        [
            InlineKeyboardButton(
                text=msg.TXT_PROFILE_MEMORY_BUTTON,
                callback_data=msg.CB_OPEN_MEMORY,
            )
        ],
        [
            InlineKeyboardButton(
                text=msg.TXT_REVIEW_BUTTON,
                callback_data=msg.CB_LEAVE_REVIEW,
            )
        ],
    ]
    show_duo = is_duo_owner if is_ultra_owner is None else is_ultra_owner
    if show_duo:
        rows.append(
            [
                InlineKeyboardButton(
                    text=msg.TXT_PROFILE_DUO_BUTTON,
                    callback_data=msg.CB_OPEN_FAMILY,
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
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


def start_paywall_markup() -> InlineKeyboardMarkup:
    """Hard Paywall: подписка на канал + проверка (принятие оферты по кнопке 2)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_PAYWALL_SUBSCRIBE_BTN,
                    url=settings.channel_url,
                ),
            ],
            [
                InlineKeyboardButton(
                    text=msg.TXT_PAYWALL_CHECK_BTN,
                    callback_data=msg.CB_CHECK_SUBSCRIPTION,
                ),
            ],
        ]
    )


def terms_accept_keyboard() -> InlineKeyboardMarkup:
    """Устаревший экран оферты — тот же callback, что и paywall."""
    return start_paywall_markup()


def support_faq_keyboard() -> InlineKeyboardMarkup:
    """Уровень 1: главный экран поддержки (см. ``platforms.support_center``)."""
    from platforms.support_center import support_main_keyboard

    return support_main_keyboard()
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
    """Тот же набор кнопок, что на экране-заслонке /start."""
    return start_paywall_markup()


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


def hd_match_family_picker_keyboard(
    members: list[tuple[int, str]],
) -> InlineKeyboardMarkup:
    """Клавиатура выбора партнёра из ULTRA-семьи для расчёта Compatibility.

    `members` — список (member_id, display_label). Чтобы попасть сюда, у member
    уже должны быть валидные ``hd_birth_data``. Внизу всегда — кнопка ручного ввода.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for member_id, label in members:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"👤 {label}",
                    callback_data=f"{msg.CB_HD_MATCH_FAMILY_PREFIX}{member_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="✍️ Ввести данные партнёра вручную",
                callback_data=msg.CB_HD_MATCH_MANUAL,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def service_rules_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📜 Публичная оферта", url=settings.service_offer_url)],
            [InlineKeyboardButton(text="🔒 Политика конфиденциальности", url=settings.privacy_policy_url)],
            [InlineKeyboardButton(text="🔁 Условия подписки", url=settings.subscription_terms_url)],
            [InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data=msg.CB_BACK_MAIN)],
        ]
    )
