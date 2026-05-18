"""Telegram-интерфейс (aiogram). Тексты — в content/messages.py; логика — в services/."""
from __future__ import annotations

import asyncio
import html
import logging
import random
import time
from collections.abc import Awaitable, Callable
from io import BytesIO
from pathlib import Path
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    BufferedInputFile,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    TelegramObject,
)
from aiogram.types import PreCheckoutQuery
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from config import settings
from content import messages as msg
from services import hd_service
from services import payments_catalog as paycat
from services.dialog_write_worker import start_dialog_write_worker
from services.hd_logic import (
    HD_REPORT_COST,
    MATCH_REPORT_COST,
    PRICE_UPSCALE,
    birth_data_minimum_for_advice,
    daily_advice_user_profile_from_repo_user,
    change_user_crystals,
    create_pdf,
    format_premium_report,
    generate_daily_forecast,
    generate_premium_report,
    get_calculated_gates,
    get_user,
    parse_birth_for_daily_advice,
    parse_hd_request,
    parse_match_request,
    premium_report_from_json,
    premium_report_to_json,
    today_iso,
    try_consume_crystals,
    update_user,
)
from services.repository import (
    add_promo_code,
    check_and_spend as spend_crystals,
    clear_user_dialog_and_memory,
    commit_daily_advice,
    ensure_user,
    get_sales_stats,
    get_user_row,
    init_db,
    list_all_user_ids,
    rollback_daily_advice,
    sales_stats_as_dict,
    try_begin_daily_advice,
    update_balance,
)
from services.telegram_safe_text import sanitize_telegram_plain_text
from services.use_cases.animate_generation_turn import AnimateGenOutcome, run_animate_generation_turn
from platforms.telegram_chat_action import chat_action_loop
from platforms.telegram_chat_stream import create_throttled_stream_reply
from platforms.telegram_chunks import answer_chat_text
from services.app_logging import setup_logging
from services.use_cases.chat_turn import ChatTurnOutcome, run_chat_turn
from services.use_cases.music_generation_turn import MusicGenOutcome, run_music_generation_turn
from services.use_cases.cabinet_turn import build_cabinet_view
from services.use_cases.payment_invoice_turn import InvoiceBuildOutcome, build_payment_invoice_draft
from services.use_cases.payment_shop_turn import build_tariffs_entry_text
from services.use_cases.start_ui_turn import start_messages_link_preview_off
from services.use_cases.tariff_shop_nav_turn import TariffShopNavOutcome, resolve_tariff_shop_callback
from services.use_cases.payment_turn import PaymentApplyOutcome, run_successful_payment_apply
from services.use_cases.start_turn import run_start_turn
from services.use_cases.photo_generation_turn import PhotoGenOutcome, run_photo_generation_turn
from services.use_cases.promo_turn import PromoOutcome, run_promo_redeem
from services.use_cases.video_generation_turn import VideoGenOutcome, run_video_generation_turn


class _HelpInstructionWordFilter(BaseFilter):
    """Точное сообщение «помощь» / «help» (без учёта регистра и пробелов по краям), не команда и не кнопка меню."""

    async def __call__(self, message: Message) -> bool:
        raw = message.text
        if not raw or raw.startswith("/"):
            return False
        if raw in _reply_menu_button_texts():
            return False
        return raw.strip().lower() in msg.HELP_TRIGGER_WORDS


class UserFlow(StatesGroup):
    waiting_for_text_prompt = State()
    waiting_for_photo = State()
    waiting_for_video = State()
    waiting_for_music = State()
    waiting_for_animate = State()
    waiting_for_upscale_photo = State()
    waiting_hd_birth_data = State()
    waiting_advice_birth = State()
    WAITING_PARTNER_DATA = State()
    waiting_promo_code = State()


class AdminStates(StatesGroup):
    waiting_for_crystals = State()
    waiting_for_broadcast = State()


logger = logging.getLogger(__name__)


class DailyResetMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is not None:
            await ensure_user(user.id, getattr(user, "username", None))
        return await handler(event, data)


def _invite_switch_query() -> str:
    q = msg.INVITE_SWITCH_QUERY_TEMPLATE.format(
        bot_username=settings.telegram_bot_username.lstrip("@"),
    )
    return q[:256]


def _instruction_tariffs_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.BTN_TARIFFS,
                    callback_data=msg.CB_RESULT_PREMIUM,
                )
            ]
        ]
    )


async def send_same_as_instruction_button(target: Message) -> None:
    """Тот же ответ, что по инструкции: интро секции + текст + inline «🚀 Тарифы»."""
    await target.answer(msg.TXT_SECTION_INTRO)
    await target.answer(msg.TXT_INSTRUCTION, reply_markup=_instruction_tariffs_markup())


def is_admin_user(user_id: int | None) -> bool:
    """Telegram user id в ``settings.admin_ids`` (из ADMIN_IDS в .env)."""
    return user_id is not None and user_id in set(settings.admin_ids)


async def notify_admins_about_payment(
    bot: Bot,
    payer_id: int,
    tariff_name: str,
    reward_description: str,
) -> None:
    """Отправляет финансовый отчёт владельцам бота при покупке тарифа."""
    report_text = msg.format_admin_payment_notice_html(
        payer_id, tariff_name, reward_description
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=report_text,
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            logger.exception(
                "failed_send_admin_payment_notice admin_id=%s payer_id=%s",
                admin_id,
                payer_id,
            )


def _reply_menu_button_texts() -> frozenset[str]:
    """Все подписи Reply-кнопок главного меню (для фильтров чата и отмены FSM)."""
    return frozenset({*msg.USER_MAIN_MENU_BUTTONS, msg.ADMIN_MAIN_MENU_BUTTON})


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
            [InlineKeyboardButton(text="🔍 UPSCALE фото — 1 💎", callback_data=msg.CB_UPSCALE_START)],
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


def _admin_telegram_url() -> str:
    """Ссылка на личный чат администратора (ADMIN_USERNAME в .env)."""
    admin = (settings.admin_username or "mulendeeva_ai").lstrip("@").strip()
    return f"https://t.me/{admin}"


def support_faq_keyboard() -> InlineKeyboardMarkup:
    """FAQ: быстрый переход к администратору."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_FAQ_ADMIN_CONTACT,
                    url=_admin_telegram_url(),
                )
            ],
        ]
    )


def support_menu() -> InlineKeyboardMarkup:
    admin = settings.admin_username.lstrip("@").strip()
    support = settings.support_bot_username.lstrip("@")
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="💬 Написать в поддержку", url=f"https://t.me/{support}")],
    ]
    if admin:
        rows.append(
            [
                InlineKeyboardButton(
                    text=msg.TXT_FAQ_ADMIN_CONTACT,
                    url=f"https://t.me/{admin}",
                )
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="📜 Правила сервиса", callback_data=msg.CB_SERVICE_RULES)],
            [InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data=msg.CB_BACK_MAIN)],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
                    text=msg.TXT_HD_INLINE_FULL_REPORT.format(cost=HD_REPORT_COST),
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


def build_dispatcher() -> tuple[Bot, Dispatcher]:
    def _is_admin(user_id: int) -> bool:
        return is_admin_user(user_id)

    bot = Bot(token=settings.tg_token)
    dp = Dispatcher()
    daily_reset_middleware = DailyResetMiddleware()
    dp.message.outer_middleware(daily_reset_middleware)
    dp.callback_query.outer_middleware(daily_reset_middleware)
    dp.pre_checkout_query.outer_middleware(daily_reset_middleware)

    subscribed_cache: dict[int, float] = {}
    SUBSCRIPTION_TTL_SEC = 60.0

    async def is_subscribed(user_id: int) -> bool:
        try:
            member = await bot.get_chat_member(chat_id=settings.channel_id, user_id=user_id)
            return member.status not in ("left", "kicked")
        except Exception:
            return True

    async def is_subscribed_cached(user_id: int) -> bool:
        now = time.monotonic()
        cached_at = subscribed_cache.get(user_id)
        if cached_at is not None and (now - cached_at) < SUBSCRIPTION_TTL_SEC:
            return True
        ok = await is_subscribed(user_id)
        if ok:
            subscribed_cache[user_id] = now
        else:
            subscribed_cache.pop(user_id, None)
        return ok

    class ChannelGateMiddleware(BaseMiddleware):
        """Мягкая проверка подписки: пропускаем /start и сам callback проверки, остальное — за гейтом."""

        async def __call__(
            self,
            handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: dict[str, Any],
        ) -> Any:
            if isinstance(event, types.Message):
                text = (event.text or "").strip()
                if text.startswith("/start"):
                    return await handler(event, data)
            elif isinstance(event, types.CallbackQuery):
                if (event.data or "") == msg.CB_CHECK_SUBSCRIPTION:
                    return await handler(event, data)
            else:
                return await handler(event, data)

            user = data.get("event_from_user")
            if user is None:
                return await handler(event, data)
            if await is_subscribed_cached(user.id):
                return await handler(event, data)

            markup = channel_gate_markup()
            if isinstance(event, types.Message):
                await event.answer(msg.TXT_CHANNEL_GATE, reply_markup=markup)
            elif isinstance(event, types.CallbackQuery):
                await event.message.answer(msg.TXT_CHANNEL_GATE, reply_markup=markup)
                await event.answer()
            return None

    async def check_and_spend(target: Message, user_id: int, amount: int) -> bool:
        user = await get_user(user_id)
        balance = int(user["crystals"] or 0)
        if balance < amount:
            await target.answer(
                msg.TXT_NOT_ENOUGH_CRYSTALS.format(amount=amount, balance=balance),
                reply_markup=paycat.shop_packages_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return False
        ok = await spend_crystals(user_id, amount)
        if not ok:
            user = await get_user(user_id)
            await target.answer(
                msg.TXT_NOT_ENOUGH_CRYSTALS.format(amount=amount, balance=int(user["crystals"] or 0)),
                reply_markup=paycat.shop_packages_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        return ok

    channel_gate_middleware = ChannelGateMiddleware()
    dp.message.outer_middleware(channel_gate_middleware)
    dp.callback_query.outer_middleware(channel_gate_middleware)

    @dp.message(Command("start"))
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        uname = message.from_user.username if message.from_user else None
        await run_start_turn(
            settings,
            message.from_user.id,
            uname,
            message.text,
            is_subscribed=is_subscribed,
        )
        no_preview = start_messages_link_preview_off()
        await message.answer(
            msg.TXT_START_WELCOME,
            parse_mode=ParseMode.HTML,
            link_preview_options=no_preview,
        )
        await message.answer(
            msg.TXT_START_MAIN_MENU_PROMPT,
            reply_markup=main_menu(message.from_user.id),
        )

    @dp.callback_query(F.data == msg.CB_CHECK_SUBSCRIPTION)
    async def check_subscription(callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        subscribed_cache.pop(uid, None)
        if await is_subscribed_cached(uid):
            await callback.message.answer(
                msg.TXT_CHANNEL_GATE_OK,
                reply_markup=main_menu(uid),
            )
            await callback.answer()
            return
        await callback.answer(msg.TXT_CHANNEL_GATE_FAIL, show_alert=True)

    @dp.message(Command("reset"))
    async def cmd_reset_dialog(message: Message, state: FSMContext) -> None:
        await state.clear()
        await clear_user_dialog_and_memory(message.from_user.id)
        await message.answer(msg.TXT_RESET_OK)

    @dp.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await send_same_as_instruction_button(message)

    async def start_match_flow(target: Message, user_id: int, state: FSMContext) -> None:
        user = await get_user(user_id)
        has_pro = bool(user["has_pro_analysis"]) if "has_pro_analysis" in user.keys() else False
        if not has_pro:
            await target.answer(msg.TXT_MATCH_LOCKED, reply_markup=hd_menu(False))
            return
        crystals = int(user["crystals"] or 0)
        if crystals < MATCH_REPORT_COST:
            await target.answer(msg.TXT_MATCH_INSUFFICIENT_CRYSTALS, reply_markup=paycat.shop_packages_keyboard())
            return
        own_birth_data = (user["hd_birth_data"] or "").strip() if "hd_birth_data" in user.keys() else ""
        await state.update_data(match_own_birth_data=own_birth_data or None)
        await state.set_state(UserFlow.WAITING_PARTNER_DATA)
        await target.answer(msg.TXT_MATCH_ASK_SECOND if own_birth_data else msg.TXT_MATCH_ASK_BOTH)

    @dp.message(Command("match"))
    async def cmd_match(message: Message, state: FSMContext) -> None:
        await start_match_flow(message, message.from_user.id, state)

    @dp.message(Command("admin"))
    @dp.message(F.text == msg.ADMIN_MAIN_MENU_BUTTON)
    async def show_admin_panel(message: Message, state: FSMContext) -> None:
        uid = message.from_user.id
        if not _is_admin(uid):
            await message.answer(msg.TXT_ADMIN_DENIED)
            return
        await state.clear()
        logger.info("admin_panel_open user_id=%s", uid)
        await message.answer(msg.TXT_ADMIN_PANEL, reply_markup=get_admin_inline_keyboard())

    @dp.message(Command("debug_pay"))
    async def admin_debug_pay(message: Message) -> None:
        uid = message.from_user.id
        if not _is_admin(uid):
            await message.answer(msg.TXT_ADMIN_DENIED)
            return
        await update_balance(uid, "crystals", 100)
        row = await get_user_row(uid)
        logger.info("admin_debug_pay user_id=%s", uid)
        await message.answer(f"Тестовое пополнение выполнено: +100 💎. Баланс: {row.crystals} 💎")

    @dp.message(Command("give_energy"))
    async def admin_give_energy(message: Message) -> None:
        uid = message.from_user.id
        if not _is_admin(uid):
            await message.answer(msg.TXT_ADMIN_DENIED)
            return
        parts = (message.text or "").split()
        if len(parts) != 3:
            await message.answer("Формат: /give_energy [user_id] [amount]")
            return
        try:
            target_id = int(parts[1])
            amount = int(parts[2])
        except ValueError:
            await message.answer("Неверный формат чисел.")
            return
        if amount <= 0:
            await message.answer("amount должен быть положительным числом.")
            return
        await update_balance(target_id, "energy", amount)
        logger.info("admin_give_energy admin_id=%s target_id=%s amount=%s", uid, target_id, amount)
        await message.answer(f"Готово: user_id={target_id}, начислено {amount} ⚡")

    @dp.message(Command("add_promo"))
    async def admin_add_promo(message: Message) -> None:
        uid = message.from_user.id
        if not _is_admin(uid):
            await message.answer(msg.TXT_ADMIN_DENIED)
            return
        parts = (message.text or "").split()
        if len(parts) != 4:
            await message.answer("Формат: /add_promo [code] [reward] [uses]")
            return
        code = parts[1].strip().upper()
        try:
            reward = int(parts[2])
            uses = int(parts[3])
        except ValueError:
            await message.answer("reward/uses должны быть числами.")
            return
        ok = await add_promo_code(code, reward, uses)
        if not ok:
            await message.answer("Не удалось создать промокод. Проверьте параметры.")
            return
        logger.info("admin_add_promo admin_id=%s code=%s reward=%s uses=%s", uid, code, reward, uses)
        await message.answer(f"Промокод {code} создан: +{reward} ⚡, активаций: {uses}")

    @dp.callback_query(F.data == msg.CB_ADMIN_STATS)
    async def process_admin_stats(callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        if not _is_admin(uid):
            await callback.answer(msg.TXT_ADMIN_DENIED, show_alert=True)
            return

        stats = sales_stats_as_dict(await get_sales_stats())
        stats_text = msg.format_admin_stats_html(stats)
        markup = get_admin_inline_keyboard()

        logger.info("admin_stats admin_id=%s", uid)
        try:
            await callback.message.edit_text(
                stats_text,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest:
            await callback.message.answer(
                stats_text,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
        await callback.answer()

    @dp.callback_query(
        F.data.in_({msg.CB_ADMIN_GIVE_CRYSTALS, msg.CB_ADMIN_GRANT_CRYSTALS, "admin_grant_crystals"})
    )
    async def admin_crystals_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_admin(callback.from_user.id):
            await callback.answer(msg.TXT_ADMIN_DENIED, show_alert=True)
            return
        await state.set_state(AdminStates.waiting_for_crystals)
        logger.info("admin_crystals_start admin_id=%s", callback.from_user.id)
        await callback.message.answer(msg.TXT_ADMIN_GRANT_PROMPT)
        await callback.answer()

    @dp.message(AdminStates.waiting_for_crystals, F.text)
    async def admin_crystals_process(message: Message, state: FSMContext) -> None:
        uid = message.from_user.id
        if not _is_admin(uid):
            await message.answer(msg.TXT_ADMIN_DENIED)
            return

        raw = (message.text or "").strip()
        if raw == "/cancel" or raw.lower() == "cancel":
            await state.clear()
            await message.answer(
                msg.TXT_ADMIN_GRANT_CANCELLED,
                reply_markup=main_menu(uid),
            )
            return

        try:
            parts = raw.split()
            if len(parts) != 2:
                raise ValueError("expected two parts")
            target_uid = int(parts[0])
            amount = int(parts[1])
            if amount <= 0:
                raise ValueError("amount must be positive")

            await update_balance(target_uid, "crystals", amount)
            logger.info("admin_grant_crystals admin_id=%s target_id=%s amount=%s", uid, target_uid, amount)

            await message.answer(
                msg.TXT_ADMIN_GRANT_DONE.format(user_id=target_uid, amount=amount),
                reply_markup=main_menu(uid),
            )
            try:
                await message.bot.send_message(
                    chat_id=target_uid,
                    text=msg.TXT_ADMIN_GRANT_USER_NOTIFY.format(amount=amount),
                )
            except Exception:
                logger.warning("admin_grant_notify_failed target_id=%s", target_uid)

            await state.clear()
        except Exception:
            await message.reply(msg.TXT_ADMIN_GRANT_INVALID)

    @dp.callback_query(
        F.data.in_({msg.CB_ADMIN_START_BROADCAST, msg.CB_ADMIN_BROADCAST, "admin_broadcast"})
    )
    async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
        uid = callback.from_user.id
        if not _is_admin(uid):
            await callback.answer(msg.TXT_ADMIN_DENIED, show_alert=True)
            return
        await state.set_state(AdminStates.waiting_for_broadcast)
        logger.info("admin_broadcast_start admin_id=%s", uid)
        await callback.message.answer(msg.TXT_ADMIN_BROADCAST_PROMPT)
        await callback.answer()

    @dp.message(AdminStates.waiting_for_broadcast)
    async def admin_broadcast_process(message: Message, state: FSMContext) -> None:
        uid = message.from_user.id
        if not _is_admin(uid):
            await message.answer(msg.TXT_ADMIN_DENIED)
            return
        if message.text and message.text.split()[0] == "/cancel":
            await state.clear()
            await message.answer(msg.TXT_ADMIN_BROADCAST_CANCEL)
            return
        if not message.photo and not (message.text and message.text.strip()):
            await message.answer(msg.TXT_ADMIN_BROADCAST_EMPTY)
            return

        all_user_ids = await list_all_user_ids()
        delivered = 0
        errors = 0
        status_msg = await message.answer(
            msg.TXT_ADMIN_BROADCAST_RUNNING.format(count=len(all_user_ids))
        )
        await state.clear()

        for target_uid in all_user_ids:
            try:
                if message.photo:
                    await message.bot.send_photo(
                        chat_id=target_uid,
                        photo=message.photo[-1].file_id,
                        caption=message.caption,
                    )
                else:
                    await message.bot.send_message(chat_id=target_uid, text=message.text)
                delivered += 1
                await asyncio.sleep(0.05)
            except Exception:
                errors += 1

        logger.info(
            "admin_broadcast_done admin_id=%s delivered=%s failed=%s",
            uid,
            delivered,
            errors,
        )
        await status_msg.edit_text(
            msg.TXT_ADMIN_BROADCAST_DONE.format(delivered=delivered, errors=errors),
            parse_mode="HTML",
        )

    @dp.message(F.text == msg.BTN_DAILY_ADVICE)
    async def daily_advice_from_menu(message: Message, state: FSMContext) -> None:
        await _send_daily_advice(message, message.from_user.id, state)

    @dp.message(F.text == msg.BTN_CREATE)
    async def open_create_menu(message: Message) -> None:
        await message.answer(msg.TXT_SELECT_TOOL, reply_markup=create_menu())

    @dp.message(F.text == msg.BTN_HD_SECTION)
    async def open_hd_from_main_menu(message: Message) -> None:
        user = await get_user(message.from_user.id)
        has_pro = bool(user["has_pro_analysis"]) if "has_pro_analysis" in user.keys() else False
        await message.answer(
            msg.TXT_HD_SECTION_INTRO,
            reply_markup=hd_menu(has_pro),
            parse_mode=ParseMode.HTML,
        )

    @dp.message(F.text == msg.BTN_PROFILE)
    async def show_profile_from_short_menu(message: Message) -> None:
        await message.answer(msg.TXT_SECTION_INTRO)
        view = await build_cabinet_view(settings, message.from_user.id)
        await message.answer(view.text, reply_markup=cabinet_keyboard())

    @dp.message(F.text == msg.BTN_TARIFFS)
    async def show_tariffs_from_short_menu(message: Message) -> None:
        await message.answer(msg.TXT_SECTION_INTRO)
        await message.answer(build_tariffs_entry_text(), reply_markup=paycat.shop_packages_keyboard())

    @dp.message(F.text.in_({msg.BTN_SUPPORT, msg.BTN_SUPPORT_LEGACY}))
    async def show_support_and_faq(message: Message) -> None:
        await message.answer(
            msg.TXT_FAQ_SUPPORT,
            parse_mode=ParseMode.HTML,
            reply_markup=support_faq_keyboard(),
            link_preview_options=types.LinkPreviewOptions(is_disabled=True),
        )

    @dp.callback_query(F.data == msg.CB_BACK_CREATE)
    async def back_create(callback: CallbackQuery) -> None:
        await callback.message.answer(msg.TXT_SELECT_TOOL, reply_markup=create_menu())
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_HD_SECTION)
    async def open_hd_section(callback: CallbackQuery) -> None:
        user = await get_user(callback.from_user.id)
        has_pro = bool(user["has_pro_analysis"]) if "has_pro_analysis" in user.keys() else False
        await callback.message.answer(
            msg.TXT_HD_SECTION_INTRO,
            reply_markup=hd_menu(has_pro),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_HD_REPORT_OPEN)
    async def open_existing_hd_report(callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        user = await get_user(uid)
        report = premium_report_from_json(user["hd_report_json"] if "hd_report_json" in user.keys() else None)
        if report is None:
            await callback.answer(msg.TXT_HD_REPORT_NOT_FOUND_ALERT, show_alert=True)
            return
        await callback.message.answer(
            msg.TXT_HD_REPORT_READY,
            reply_markup=hd_report_sections_markup(),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_CREATE_TEXT)
    async def create_text_hint(callback: CallbackQuery) -> None:
        await callback.message.answer(msg.TXT_CREATE_TEXT_HINT, reply_markup=text_role_menu())
        await callback.answer()

    @dp.callback_query(F.data.startswith(msg.CB_TEXT_ROLE_PREFIX))
    async def pick_text_role(callback: CallbackQuery, state: FSMContext) -> None:
        role_id = (callback.data or "").removeprefix(msg.CB_TEXT_ROLE_PREFIX)
        role_map = {rid: label for label, rid in msg.TEXT_ROLES}
        if role_id not in role_map:
            await callback.answer("Неизвестный режим.", show_alert=True)
            return
        row = await get_user_row(callback.from_user.id)
        has_premium_access = row.crystals > 0 or row.balance_crystals > 0 or row.tariff.strip().lower() != "free"
        if role_id in msg.PREMIUM_TEXT_ROLE_IDS and not has_premium_access:
            await callback.message.answer(msg.TXT_PREMIUM_ROLE_LOCKED, reply_markup=paycat.shop_packages_keyboard())
            await callback.answer(msg.TXT_PREMIUM_ROLE_LOCKED, show_alert=True)
            return
        await state.update_data(text_role=role_id)
        await state.set_state(UserFlow.waiting_for_text_prompt)
        await callback.message.answer(msg.TXT_TEXT_ROLE_SELECTED.format(role=role_map[role_id]))
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_CREATE_IMAGE)
    async def create_image_menu(callback: CallbackQuery) -> None:
        await callback.message.answer(msg.TXT_IMAGE_INTRO, reply_markup=image_model_menu())
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_UPSCALE_START)
    async def upscale_start_callback(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(UserFlow.waiting_for_upscale_photo)
        await callback.message.answer(msg.TXT_UPSCALE_HINT)
        await callback.answer()

    @dp.callback_query(F.data.startswith(msg.CB_IMG_PREFIX))
    async def pick_image_model(callback: CallbackQuery, state: FSMContext) -> None:
        mid = callback.data[len(msg.CB_IMG_PREFIX) :]
        if mid not in msg.IMAGE_MODEL_IDS:
            await callback.answer(msg.TXT_UNKNOWN_IMAGE_MODEL, show_alert=True)
            return
        label = next(lbl for lbl, i in msg.IMAGE_MODELS if i == mid)
        await state.update_data(image_model_id=mid, image_model_label=label)
        await state.set_state(UserFlow.waiting_for_photo)
        await callback.message.answer(msg.TXT_CREATE_IMAGE_AFTER_MODEL)
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_CREATE_ANIMATE)
    async def create_animate_start(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.message.answer(msg.TXT_CREATE_ANIMATE_HINT)
        await state.set_state(UserFlow.waiting_for_animate)
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_CREATE_VIDEO)
    async def create_video_start(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.message.answer(msg.TXT_CREATE_VIDEO_HINT)
        await state.set_state(UserFlow.waiting_for_video)
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_CREATE_MUSIC)
    async def create_music_start(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.message.answer(msg.TXT_CREATE_MUSIC_HINT)
        await state.set_state(UserFlow.waiting_for_music)
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_MATCH_START)
    async def match_start(callback: CallbackQuery, state: FSMContext) -> None:
        await start_match_flow(callback.message, callback.from_user.id, state)
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_HD_PREMIUM_BUY)
    async def hd_premium_buy(callback: CallbackQuery, state: FSMContext) -> None:
        uid = callback.from_user.id
        user = await get_user(uid)
        has_pro = bool(user["has_pro_analysis"]) if "has_pro_analysis" in user.keys() else False
        if has_pro:
            await callback.message.answer(
                msg.TXT_HD_ALREADY_PURCHASED,
                reply_markup=hd_menu(True),
                parse_mode=ParseMode.HTML,
            )
            await callback.answer()
            return
        if not await is_subscribed(uid):
            await callback.message.answer(
                msg.TXT_HD_NEED_CHANNEL,
                reply_markup=channel_subscribe_markup(),
                parse_mode=ParseMode.HTML,
            )
            await callback.answer(msg.TXT_HD_NEED_CHANNEL_ALERT, show_alert=True)
            return
        crystals = int(user["crystals"] or 0)
        if crystals < HD_REPORT_COST:
            await callback.message.answer(
                msg.TXT_HD_INSUFFICIENT_CRYSTALS.format(cost=HD_REPORT_COST),
                reply_markup=paycat.shop_packages_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            await callback.answer(
                msg.TXT_HD_INSUFFICIENT_CRYSTALS_ALERT.format(cost=HD_REPORT_COST),
                show_alert=True,
            )
            return
        await state.set_state(UserFlow.waiting_hd_birth_data)
        await callback.message.answer(
            msg.TXT_HD_ASK_BIRTH_DATA.format(cost=HD_REPORT_COST),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()

    async def _send_daily_advice(target: Message, uid: int, state: FSMContext | None = None) -> None:
        user = await get_user(uid)
        today = today_iso()
        if (user["last_free_date"] or "") == today:
            await target.answer(msg.TXT_HD_FREE_ADVICE_USED, parse_mode=ParseMode.HTML)
            return
        if not await try_begin_daily_advice(uid):
            await target.answer(msg.TXT_HD_FREE_ADVICE_USED, parse_mode=ParseMode.HTML)
            return
        user_profile = daily_advice_user_profile_from_repo_user(user)
        if user_profile is None:
            await rollback_daily_advice(uid)
            if state is not None:
                await state.set_state(UserFlow.waiting_advice_birth)
                await target.answer(msg.TXT_ADVICE_BIRTH_ASK, parse_mode=ParseMode.HTML)
                return
            await target.answer(msg.TXT_ADVICE_NEED_STATE, parse_mode=ParseMode.HTML)
            return
        animation_texts = (msg.TXT_HD_DAILY_ANIM_1, msg.TXT_HD_DAILY_ANIM_2, msg.TXT_HD_DAILY_ANIM_3)
        forecast_message = await target.answer(animation_texts[0])
        stop_animation = asyncio.Event()

        async def animate_waiting() -> None:
            idx = 1
            while not stop_animation.is_set():
                try:
                    await asyncio.wait_for(stop_animation.wait(), timeout=0.5)
                    break
                except TimeoutError:
                    pass
                try:
                    await forecast_message.edit_text(animation_texts[idx % len(animation_texts)])
                except TelegramBadRequest:
                    pass
                idx += 1

        animation_task = asyncio.create_task(animate_waiting())
        full_text = ""
        last_sent_text = ""
        edit_count = 0
        try:
            async with chat_action_loop(bot, target.chat.id, "typing"):
                async for chunk in generate_daily_forecast(
                    user_profile,
                    current_cta_text=msg.TXT_HD_DAILY_ADVICE_CTA,
                ):
                    if not full_text:
                        stop_animation.set()
                        await animation_task
                    full_text += chunk
                    safe_text = sanitize_telegram_plain_text(full_text)
                    if safe_text != last_sent_text:
                        try:
                            await forecast_message.edit_text(safe_text or "…")
                        except TelegramBadRequest:
                            pass
                        last_sent_text = safe_text
                        edit_count += 1
                        if edit_count % 3 == 0:
                            await asyncio.sleep(0.3)
                stop_animation.set()
                await animation_task
                final_text = sanitize_telegram_plain_text(full_text.strip())
                if not final_text:
                    raise RuntimeError("Gemini returned empty daily advice")
                if final_text != last_sent_text:
                    try:
                        await forecast_message.edit_text(final_text)
                    except TelegramBadRequest:
                        pass
                await commit_daily_advice(uid)
        except Exception:
            stop_animation.set()
            animation_task.cancel()
            await rollback_daily_advice(uid)
            logger.exception("hd_free_advice_failed user_id=%s", uid)
            try:
                await forecast_message.edit_text(msg.TXT_HD_FREE_ADVICE_FAILED, parse_mode=ParseMode.HTML)
            except TelegramBadRequest:
                pass

    @dp.message(UserFlow.waiting_advice_birth, Command("cancel"))
    async def advice_birth_cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(msg.TXT_ADVICE_BIRTH_CANCELLED, parse_mode=ParseMode.HTML)

    @dp.message(UserFlow.waiting_advice_birth, F.text)
    async def advice_birth_save(message: Message, state: FSMContext) -> None:
        raw = (message.text or "").strip()
        menus = _reply_menu_button_texts()
        if raw in menus:
            await state.clear()
            await message.answer(msg.TXT_ADVICE_BIRTH_CANCELLED, parse_mode=ParseMode.HTML)
            return
        if not raw:
            await message.answer(msg.TXT_ADVICE_BIRTH_INVALID, parse_mode=ParseMode.HTML)
            return
        if not birth_data_minimum_for_advice(raw):
            await message.answer(msg.TXT_ADVICE_BIRTH_INVALID, parse_mode=ParseMode.HTML)
            return
        parsed = parse_birth_for_daily_advice(raw)
        await update_user(
            message.from_user.id,
            advice_birth_data=raw,
            advice_user_role=parsed["user_role"],
        )
        await state.clear()
        await message.answer(msg.TXT_ADVICE_BIRTH_SAVED, parse_mode=ParseMode.HTML)
        await _send_daily_advice(message, message.from_user.id, None)

    @dp.callback_query(F.data == msg.CB_HD_FREE_ADVICE)
    async def hd_free_advice(callback: CallbackQuery, state: FSMContext) -> None:
        uid = callback.from_user.id
        user = await get_user(uid)
        if (user["last_free_date"] or "") == today_iso():
            await callback.answer(msg.TXT_HD_FREE_ADVICE_USED_ALERT, show_alert=True)
            return
        await callback.answer()
        await _send_daily_advice(callback.message, uid, state)

    @dp.message(UserFlow.waiting_hd_birth_data, F.text)
    async def hd_premium_process(message: Message, state: FSMContext) -> None:
        uid = message.from_user.id
        raw = (message.text or "").strip()
        if not raw:
            await message.answer(msg.TXT_HD_EMPTY_DATA, parse_mode=ParseMode.HTML)
            return
        if not await is_subscribed(uid):
            await message.answer(
                msg.TXT_HD_NEED_CHANNEL,
                reply_markup=channel_subscribe_markup(),
                parse_mode=ParseMode.HTML,
            )
            return
        if not await check_and_spend(message, uid, HD_REPORT_COST):
            await state.clear()
            return

        await message.answer(msg.TXT_HD_PROCESSING, parse_mode=ParseMode.HTML)
        try:
            async with chat_action_loop(bot, message.chat.id, "typing"):
                hd_type, birth_data = parse_hd_request(raw)
                report = await generate_premium_report(hd_type, birth_data)
            if not report:
                raise RuntimeError("Gemini returned empty HD report")
            await update_user(
                uid,
                hd_report_json=premium_report_to_json(report),
                hd_type=hd_type,
                hd_birth_data=birth_data,
                has_pro_analysis=1,
            )
            row = await get_user_row(uid)
            await message.answer(
                msg.TXT_HD_PAYMENT_OK.format(cost=HD_REPORT_COST, balance=row.crystals),
                parse_mode=ParseMode.HTML,
            )
            await message.answer(
                msg.TXT_HD_REPORT_READY,
                reply_markup=hd_report_sections_markup(),
                parse_mode=ParseMode.HTML,
            )
            await message.answer(
                msg.TXT_HD_PRO_UNLOCKED,
                reply_markup=hd_pro_unlocked_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            logger.exception("hd_premium_failed user_id=%s", uid)
            await change_user_crystals(uid, HD_REPORT_COST)
            await message.answer(
                msg.TXT_HD_FAILED,
                reply_markup=paycat.shop_packages_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        finally:
            await state.clear()

    @dp.message(UserFlow.waiting_hd_birth_data)
    async def hd_premium_need_text(message: Message) -> None:
        await message.answer(msg.TXT_HD_EMPTY_DATA, parse_mode=ParseMode.HTML)

    @dp.message(UserFlow.WAITING_PARTNER_DATA, F.text)
    async def match_process(message: Message, state: FSMContext) -> None:
        uid = message.from_user.id
        raw = (message.text or "").strip()
        if not raw:
            await message.answer(msg.TXT_MATCH_EMPTY_DATA)
            return
        data = await state.get_data()
        own_birth_data = data.get("match_own_birth_data")
        first_from_message, second_birth_data = parse_match_request(raw)
        first_birth_data = str(own_birth_data or first_from_message or "").strip()
        second_birth_data = (second_birth_data or "").strip()
        if not first_birth_data or not second_birth_data:
            await message.answer(msg.TXT_MATCH_ASK_BOTH)
            return
        user = await get_user(uid)
        await update_user(uid, match_partner_data=second_birth_data)
        if not await try_consume_crystals(uid, MATCH_REPORT_COST):
            await message.answer(msg.TXT_MATCH_INSUFFICIENT_CRYSTALS, reply_markup=paycat.shop_packages_keyboard())
            await state.clear()
            return
        await message.answer(msg.TXT_MATCH_PROCESSING)
        try:
            async with chat_action_loop(bot, message.chat.id, "typing"):
                user1_data = {
                    "type": (user["hd_type"] or "не указан") if "hd_type" in user.keys() else "не указан",
                    "gates": get_calculated_gates(first_birth_data)["gates"],
                }
                user2_data = {
                    "type": "не указан",
                    "gates": get_calculated_gates(second_birth_data)["gates"],
                }
                report = await hd_service.generate_match_report(user1_data, user2_data)
            if not report:
                raise RuntimeError("Gemini returned empty match report")
            await answer_chat_text(message, report, settings)
        except Exception:
            logger.exception("match_failed user_id=%s", uid)
            await change_user_crystals(uid, MATCH_REPORT_COST)
            await message.answer(msg.TXT_MATCH_FAILED, reply_markup=paycat.shop_packages_keyboard())
        finally:
            await state.clear()

    @dp.message(UserFlow.WAITING_PARTNER_DATA)
    async def match_need_text(message: Message) -> None:
        await message.answer(msg.TXT_MATCH_EMPTY_DATA)

    @dp.callback_query(F.data.startswith(msg.CB_HD_REPORT_PREFIX))
    async def hd_report_section(callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        user = await get_user(uid)
        report = premium_report_from_json(user["hd_report_json"] if "hd_report_json" in user.keys() else None)
        if report is None:
            await callback.answer(msg.TXT_HD_REPORT_NOT_FOUND_ALERT, show_alert=True)
            return

        section = (callback.data or "").removeprefix(msg.CB_HD_REPORT_PREFIX)
        titles = {
            "money": msg.TXT_HD_BTN_REPORT_MONEY,
            "love": msg.TXT_HD_BTN_REPORT_LOVE,
            "energy": msg.TXT_HD_BTN_REPORT_ENERGY,
            "plan": msg.TXT_HD_BTN_REPORT_PLAN,
        }
        if section == "pdf":
            pdf_path: str | None = None
            try:
                birth_data = (user["hd_birth_data"] or "").strip() if "hd_birth_data" in user.keys() else None
                async with chat_action_loop(bot, callback.message.chat.id, "upload_document"):
                    pdf_path = create_pdf(uid, format_premium_report(report), birth_data)
                    await callback.message.answer_document(
                        FSInputFile(pdf_path),
                        caption=msg.TXT_HD_PDF_CAPTION,
                        parse_mode=ParseMode.HTML,
                    )
            finally:
                if pdf_path:
                    try:
                        Path(pdf_path).unlink(missing_ok=True)
                    except OSError:
                        logger.warning("failed_remove_hd_pdf path=%s", pdf_path)
            await callback.answer()
            return

        if section not in titles:
            await callback.answer(msg.TXT_STUB_BUTTON, show_alert=True)
            return
        title = titles[section]
        body_safe = html.escape(report[section])
        await callback.message.answer(
            f"<b>{html.escape(title)}</b>\n\n{body_safe}",
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_CABINET_PROMO)
    async def cabinet_promo_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(UserFlow.waiting_promo_code)
        await callback.message.answer(msg.TXT_PROMO_ASK)
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_SHOW_INSTRUCTION)
    async def cabinet_show_instruction(callback: CallbackQuery) -> None:
        await send_same_as_instruction_button(callback.message)
        await callback.answer()

    @dp.message(UserFlow.waiting_promo_code, F.text)
    async def promo_redeem(message: Message, state: FSMContext) -> None:
        raw = (message.text or "").strip()
        if raw.startswith("/"):
            await state.clear()
            return
        pr = await run_promo_redeem(message.from_user.id, raw)
        await state.clear()
        if pr.outcome is PromoOutcome.REDEEMED:
            await message.answer(msg.TXT_PROMO_REDEEMED.format(bonus=pr.bonus_energy))
        elif pr.outcome is PromoOutcome.UNKNOWN:
            await message.answer(msg.TXT_PROMO_UNKNOWN)
        elif pr.outcome is PromoOutcome.USED:
            await message.answer(msg.TXT_PROMO_USED)
        elif pr.outcome is PromoOutcome.EXHAUSTED:
            await message.answer(msg.TXT_PROMO_EXHAUSTED)
        else:
            await message.answer(msg.TXT_PROMO_UNKNOWN)

    @dp.message(UserFlow.waiting_for_text_prompt, F.text)
    async def text_role_process(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        role_id = str(data.get("text_role") or "standard")
        uid = message.from_user.id
        raw = (message.text or "")[: settings.chat_max_message_chars]

        async with chat_action_loop(bot, message.chat.id, "typing"):
            await message.answer("Прокладываю кратчайший путь через нейроны...")
            stream_cb = (
                create_throttled_stream_reply(message, bot, settings)
                if settings.telegram_chat_streaming
                else None
            )
            result = await run_chat_turn(
                settings,
                uid,
                raw,
                stream_callback=stream_cb,
                text_role=role_id,
            )
        await state.clear()
        if result.outcome is ChatTurnOutcome.SUCCESS:
            if stream_cb is None:
                await answer_chat_text(message, result.assistant_message or "", settings)
            return
        if result.outcome is ChatTurnOutcome.EMPTY_INPUT:
            await message.answer(msg.TXT_CHAT_EMPTY)
            return
        if result.outcome is ChatTurnOutcome.CONTEXT_TOO_LARGE:
            await message.answer(msg.TXT_CHAT_CONTEXT_TOO_LARGE)
            return
        if result.outcome is ChatTurnOutcome.RATE_LIMITED:
            await message.answer(msg.TXT_CHAT_RATE_LIMIT)
            return
        if result.outcome is ChatTurnOutcome.INSUFFICIENT_BALANCE:
            await message.answer(msg.TXT_INSUFFICIENT_BALANCE, reply_markup=paycat.shop_packages_keyboard())
            return
        await message.answer(msg.TXT_GEN_JOB_FAILED)

    @dp.message(UserFlow.waiting_for_text_prompt)
    async def text_role_need_text(message: Message) -> None:
        await message.answer(msg.TXT_CREATE_TEXT_HINT, reply_markup=text_role_menu())

    @dp.message(UserFlow.waiting_for_photo, F.text)
    async def photo_process(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id
        chat_id = message.chat.id
        data = await state.get_data()
        model_id = data.get("image_model_id", "")
        label = data.get("image_model_label", "модель")
        prompt = (message.text or "").strip()
        pr = await run_photo_generation_turn(settings, bot, chat_id, user_id, model_id, label, prompt)
        if pr.outcome is PhotoGenOutcome.NEED_PROMPT:
            await message.answer(msg.TXT_CREATE_IMAGE_AFTER_MODEL)
            return
        if pr.outcome is PhotoGenOutcome.INSUFFICIENT_BALANCE:
            await message.answer(
                msg.TXT_INSUFFICIENT_BALANCE,
                reply_markup=paycat.shop_packages_keyboard(),
            )
            await state.clear()
            return
        if pr.outcome is PhotoGenOutcome.DAILY_LIMIT_EXCEEDED:
            await message.answer(
                msg.TXT_PHOTO_DAILY_LIMIT.format(limit=settings.free_daily_photo_limit),
                reply_markup=invite_limit_keyboard(),
            )
            await state.clear()
            return
        await message.answer(msg.TXT_GEN_STATUS_ACCEPTED)
        if pr.vip_priority:
            await message.answer(msg.TXT_GEN_STATUS_VIP)
        await state.clear()

    @dp.message(UserFlow.waiting_for_photo)
    async def photo_process_need_text(message: Message) -> None:
        await message.answer(msg.TXT_CREATE_IMAGE_AFTER_MODEL)

    @dp.message(UserFlow.waiting_for_video, F.text)
    async def video_process(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id
        prompt = (message.text or "").strip()
        vr = await run_video_generation_turn(settings, bot, message.chat.id, user_id, prompt)
        if vr.outcome is VideoGenOutcome.NEED_PROMPT:
            await message.answer(msg.TXT_CREATE_VIDEO_HINT)
            return
        if vr.outcome is VideoGenOutcome.FORBIDDEN_BY_TARIFF:
            deny_text = msg.TXT_UPGRADE_TO_SMART if vr.upgrade_to == "smart" else msg.TXT_UPGRADE_TO_ULTRA
            await message.answer(deny_text, reply_markup=paycat.shop_packages_keyboard())
            await state.clear()
            return
        if vr.outcome is VideoGenOutcome.INSUFFICIENT_BALANCE:
            await message.answer(
                msg.TXT_INSUFFICIENT_BALANCE,
                reply_markup=paycat.shop_packages_keyboard(),
            )
            await state.clear()
            return
        if vr.vip_priority:
            await message.answer(msg.TXT_GEN_STATUS_VIP)
        await state.clear()

    @dp.message(UserFlow.waiting_for_video)
    async def video_need_text(message: Message) -> None:
        await message.answer(msg.TXT_CREATE_VIDEO_HINT)

    @dp.message(UserFlow.waiting_for_animate, F.photo)
    async def animate_photo_process(message: Message, state: FSMContext) -> None:
        uid = message.from_user.id
        large_photo_file_id = message.photo[-1].file_id
        await state.clear()
        ar = await run_animate_generation_turn(
            uid=uid,
            telegram_file_id=large_photo_file_id,
            bot=message.bot,
            chat_id=message.chat.id,
            settings=settings,
        )
        if ar.outcome is AnimateGenOutcome.NEED_PHOTO:
            await message.answer(msg.TXT_CREATE_ANIMATE_HINT)
            return
        if ar.outcome is AnimateGenOutcome.FORBIDDEN_BY_TARIFF:
            await message.answer(msg.TXT_UPGRADE_TO_ULTRA, reply_markup=paycat.shop_packages_keyboard())
            return
        if ar.outcome is AnimateGenOutcome.INSUFFICIENT_BALANCE:
            await message.answer(
                msg.TXT_INSUFFICIENT_BALANCE,
                reply_markup=paycat.shop_packages_keyboard(),
            )

    @dp.message(UserFlow.waiting_for_animate)
    async def animate_need_photo(message: Message) -> None:
        await message.answer(msg.TXT_CREATE_ANIMATE_HINT)

    @dp.message(UserFlow.waiting_for_upscale_photo, F.photo)
    async def upscale_process(message: Message, state: FSMContext) -> None:
        uid = message.from_user.id
        if not await check_and_spend(message, uid, PRICE_UPSCALE):
            await state.clear()
            return
        await message.answer(msg.TXT_UPSCALE_PROCESSING)
        try:
            async with chat_action_loop(bot, message.chat.id, "upload_document"):
                row = await get_user_row(uid)
                photo_id = message.photo[-1].file_id
                file = await bot.get_file(photo_id)
                if not file.file_path:
                    raise RuntimeError("Telegram did not return file_path for upscale photo")
                buffer = BytesIO()
                await bot.download_file(file.file_path, buffer)
                document = BufferedInputFile(buffer.getvalue(), filename="neuromule_upscale.jpg")
                await bot.send_document(
                    message.chat.id,
                    document,
                    caption=msg.TXT_UPSCALE_SUCCESS.format(balance=row.crystals),
                )
        except Exception:
            logger.exception("upscale_failed user_id=%s", uid)
            await update_balance(uid, "crystals", PRICE_UPSCALE)
            await message.answer(msg.TXT_UPSCALE_FAILED)
        finally:
            await state.clear()

    @dp.message(UserFlow.waiting_for_upscale_photo)
    async def upscale_need_photo(message: Message) -> None:
        await message.answer(msg.TXT_UPSCALE_HINT)

    @dp.message(UserFlow.waiting_for_music, F.text)
    async def music_style_process(message: Message, state: FSMContext) -> None:
        uid = message.from_user.id
        style_prompt = (message.text or "").strip()
        await state.clear()
        mr = await run_music_generation_turn(
            uid=uid,
            style_prompt=style_prompt,
            bot=message.bot,
            chat_id=message.chat.id,
            settings=settings,
        )
        if mr.outcome is MusicGenOutcome.NEED_HINT:
            await message.answer(msg.TXT_CREATE_MUSIC_HINT)
            return
        if mr.outcome is MusicGenOutcome.FORBIDDEN_BY_TARIFF:
            deny_text = msg.TXT_ACCESS_SMART_PLUS if mr.upgrade_to == "smart" else msg.TXT_UPGRADE_TO_ULTRA
            await message.answer(deny_text, reply_markup=paycat.shop_packages_keyboard())
            return
        if mr.outcome is MusicGenOutcome.INSUFFICIENT_BALANCE:
            await message.answer(
                msg.TXT_INSUFFICIENT_BALANCE,
                reply_markup=paycat.shop_packages_keyboard(),
            )

    @dp.message(UserFlow.waiting_for_music)
    async def music_need_text(message: Message) -> None:
        await message.answer(msg.TXT_CREATE_MUSIC_HINT)

    @dp.message(Command("profile"))
    async def profile(message: Message) -> None:
        view = await build_cabinet_view(settings, message.from_user.id)
        await message.answer(view.text, reply_markup=cabinet_keyboard())

    @dp.callback_query(F.data.startswith(paycat.CB_PAY_PKG_PREFIX))
    async def pay_pick_package(callback: CallbackQuery) -> None:
        nav = resolve_tariff_shop_callback(callback.data or "")
        if nav.outcome is TariffShopNavOutcome.INVALID:
            await callback.answer(msg.TXT_PAYMENT_INVALID, show_alert=True)
            return
        if nav.outcome is TariffShopNavOutcome.SHOP_INTRO:
            try:
                await callback.message.edit_text(nav.text, reply_markup=paycat.shop_packages_keyboard())
            except TelegramBadRequest:
                await callback.message.answer(nav.text, reply_markup=paycat.shop_packages_keyboard())
            await callback.answer()
            return
        if nav.pkg_index is None:
            await callback.answer(msg.TXT_PAYMENT_INVALID, show_alert=True)
            return
        try:
            await callback.message.edit_text(nav.text, reply_markup=paycat.pay_method_keyboard(nav.pkg_index))
        except TelegramBadRequest:
            await callback.message.answer(nav.text, reply_markup=paycat.pay_method_keyboard(nav.pkg_index))
        await callback.answer()

    @dp.callback_query(F.data.startswith(paycat.CB_PAY_METHOD_PREFIX))
    async def pay_pick_method(callback: CallbackQuery) -> None:
        parsed = paycat.parse_method_callback(callback.data or "")
        if not parsed:
            await callback.answer(msg.TXT_PAYMENT_INVALID, show_alert=True)
            return
        pkg_index, method = parsed
        uid = callback.from_user.id
        inv = build_payment_invoice_draft(settings, uid, pkg_index, method)
        if inv.outcome is InvoiceBuildOutcome.NO_YOOKASSA:
            await callback.answer(msg.TXT_PAY_NO_YOOKASSA, show_alert=True)
            return
        if inv.outcome is InvoiceBuildOutcome.INVALID or inv.draft is None:
            await callback.answer(msg.TXT_PAYMENT_INVALID, show_alert=True)
            return
        d = inv.draft
        prices = [LabeledPrice(label=p.label, amount=p.amount) for p in d.prices]
        try:
            await callback.message.answer_invoice(
                title=d.title,
                description=d.description,
                payload=d.payload,
                currency=d.currency,
                prices=prices,
                provider_token=d.provider_token,
            )
        except TelegramBadRequest as e:
            await callback.answer(f"Не удалось выставить счёт: {e}", show_alert=True)
            return
        await callback.answer()

    @dp.pre_checkout_query()
    async def pre_checkout_accept(query: PreCheckoutQuery) -> None:
        await query.answer(ok=True)

    @dp.message(F.successful_payment)
    async def successful_payment_handler(message: Message) -> None:
        sp = message.successful_payment
        if not sp or not message.from_user:
            return
        fb = f"msg:{message.chat.id}:{message.message_id}"
        pay = await run_successful_payment_apply(
            message.from_user.id,
            sp.invoice_payload or "",
            sp.telegram_payment_charge_id,
            sp.provider_payment_charge_id,
            fallback_charge_id=fb,
        )
        if pay.outcome is PaymentApplyOutcome.INVALID:
            await message.answer(msg.TXT_PAYMENT_INVALID)
            return
        if pay.outcome is PaymentApplyOutcome.DUPLICATE:
            await message.answer(msg.TXT_PAYMENT_DUPLICATE)
            return
        credited_parts = []
        if pay.energy_credited:
            credited_parts.append(f"{pay.energy_credited} ⚡️")
        if pay.crystals_credited:
            credited_parts.append(f"{pay.crystals_credited} 💎")
        credited_text = " и ".join(credited_parts) or "0"
        await message.answer(msg.TXT_PAYMENT_SUCCESS.format(amount=credited_text))
        logger.info(
            "payment_success user_id=%s energy=%s crystals=%s tariff=%s",
            message.from_user.id,
            pay.energy_credited,
            pay.crystals_credited,
            pay.tariff_activated or "unknown",
        )
        await notify_admins_about_payment(
            bot,
            message.from_user.id,
            pay.tariff_activated or "unknown",
            credited_text,
        )

    @dp.callback_query(F.data == msg.CB_RESULT_PREMIUM)
    async def open_tariffs_from_result_or_instruction(callback: CallbackQuery) -> None:
        await callback.message.answer(msg.TXT_SECTION_INTRO)
        await callback.message.answer(build_tariffs_entry_text(), reply_markup=paycat.shop_packages_keyboard())
        await callback.answer()

    result_cbs = (
        msg.CB_RESULT_ANIMATE,
        msg.CB_RESULT_REPEAT_PHOTO,
        msg.CB_RESULT_HD_PRO,
        msg.CB_RESULT_GALLERY,
        msg.CB_RESULT_MP3,
        msg.CB_RESULT_EDIT_LYRICS,
    )

    @dp.callback_query(F.data.in_(result_cbs))
    async def result_buttons_stub(callback: CallbackQuery) -> None:
        await callback.answer(msg.TXT_STUB_BUTTON)

    @dp.callback_query(F.data == msg.CB_SERVICE_RULES)
    async def service_rules(callback: CallbackQuery) -> None:
        await callback.message.answer(
            msg.TXT_SERVICE_RULES.format(
                offer=settings.service_offer_url,
                privacy=settings.privacy_policy_url,
                terms=settings.subscription_terms_url,
            ),
            reply_markup=service_rules_menu(),
        )
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_BACK_MAIN)
    async def back_main(callback: CallbackQuery) -> None:
        await callback.message.answer(msg.TXT_BACK_TO_MAIN, reply_markup=main_menu(callback.from_user.id))
        await callback.answer()

    @dp.message(StateFilter(None), F.text.lower().in_(msg.EASTER_THANKS_TRIGGERS))
    async def easter_thanks(message: Message) -> None:
        await message.answer(random.choice(msg.EASTER_THANKS_REPLIES))

    @dp.message(StateFilter(None), _HelpInstructionWordFilter())
    async def help_instruction_keyword(message: Message) -> None:
        await send_same_as_instruction_button(message)

    @dp.message(
        StateFilter(None),
        F.text,
        ~F.text.startswith("/"),
        ~F.text.in_(_reply_menu_button_texts()),
    )
    async def chat_handler(message: Message) -> None:
        uid = message.from_user.id
        raw = (message.text or "")[: settings.chat_max_message_chars]
        stream_cb = (
            create_throttled_stream_reply(message, bot, settings)
            if settings.telegram_chat_streaming
            else None
        )
        async with chat_action_loop(bot, message.chat.id, "typing"):
            result = await run_chat_turn(settings, uid, raw, stream_callback=stream_cb)
        if result.outcome is ChatTurnOutcome.SUCCESS:
            if stream_cb is None:
                await answer_chat_text(message, result.assistant_message or "", settings)
            return
        if result.outcome is ChatTurnOutcome.EMPTY_INPUT:
            await message.answer(msg.TXT_CHAT_EMPTY)
            return
        if result.outcome is ChatTurnOutcome.CONTEXT_TOO_LARGE:
            await message.answer(msg.TXT_CHAT_CONTEXT_TOO_LARGE)
            return
        if result.outcome is ChatTurnOutcome.RATE_LIMITED:
            await message.answer(msg.TXT_CHAT_RATE_LIMIT)
            return
        if result.outcome is ChatTurnOutcome.INSUFFICIENT_BALANCE:
            await message.answer(
                msg.TXT_INSUFFICIENT_BALANCE,
                reply_markup=paycat.shop_packages_keyboard(),
            )
            return
        if result.outcome is ChatTurnOutcome.DAILY_LIMIT_EXCEEDED:
            await message.answer(msg.TXT_CHAT_DAILY_LIMIT, reply_markup=paycat.shop_packages_keyboard())
            return
        await message.answer(msg.TXT_GEN_JOB_FAILED)

    return bot, dp


async def run_telegram() -> None:
    setup_logging(settings)
    if not settings.tg_token:
        raise RuntimeError("Задайте TG_TOKEN в .env")
    if not settings.openrouter_key:
        raise RuntimeError("Задайте OPENROUTER_API_KEY в .env")
    await init_db(settings.promo_seeds)
    await start_dialog_write_worker()
    bot, dp = build_dispatcher()
    print(f"{settings.bot_name} telegram: polling started.")
    await dp.start_polling(bot)
