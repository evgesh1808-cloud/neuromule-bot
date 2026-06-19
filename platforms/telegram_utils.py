"""Утилиты и фильтры Telegram-платформы."""
from __future__ import annotations

import html
import logging
import re

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from config import settings
from content import messages as msg
from services import payments_catalog as paycat
from services.billing.free_tier_gates import free_blocks_premium_create
from services.use_cases.video_generation_turn import VideoGenOutcome, VideoGenResult

logger = logging.getLogger(__name__)

_TICKET_USER_ID_RE = re.compile(r"ID:\s*(?:<code>|`)(\d+)(?:</code>|`)", re.IGNORECASE)

class HelpInstructionWordFilter(BaseFilter):
    """Точное сообщение «помощь» / «help» (без учёта регистра и пробелов по краям), не команда и не кнопка меню."""

    async def __call__(self, message: Message) -> bool:
        raw = message.text
        if not raw or raw.startswith("/"):
            return False
        if raw in _reply_menu_button_texts():
            return False
        return raw.strip().lower() in msg.HELP_TRIGGER_WORDS

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
    """Все подписи Reply-кнопок навигации (для фильтров чата и отмены FSM)."""
    return frozenset({*msg.ALL_REPLY_NAV_BUTTONS, msg.ADMIN_MAIN_MENU_BUTTON})


async def _reply_video_gen_result(
    message: Message,
    vr: VideoGenResult,
    state: FSMContext | None,
) -> None:
    """Общие ответы после ``run_video_scenario_turn``."""
    if vr.outcome is VideoGenOutcome.UNKNOWN_SCENARIO:
        await message.answer(msg.TXT_GEN_JOB_FAILED)
        if state:
            await state.clear()
        return
    if vr.outcome is VideoGenOutcome.NEED_PROMPT:
        await message.answer(msg.TXT_VIDEO_NEED_PROMPT)
        return
    if vr.outcome is VideoGenOutcome.NEED_PHOTO:
        await message.answer(msg.TXT_VIDEO_NEED_PHOTO, parse_mode=ParseMode.HTML)
        return
    if vr.outcome is VideoGenOutcome.FREE_PREMIUM_BLOCKED:
        await send_free_create_blocked(target)
        if state:
            await state.clear()
        return
    if vr.outcome is VideoGenOutcome.FORBIDDEN_BY_TARIFF:
        deny_text = msg.TXT_UPGRADE_TO_SMART if vr.upgrade_to == "smart" else msg.TXT_UPGRADE_TO_ULTRA
        await message.answer(deny_text, reply_markup=paycat.shop_packages_keyboard())
        if state:
            await state.clear()
        return
    if vr.outcome is VideoGenOutcome.INSUFFICIENT_BALANCE:
        await message.answer(msg.TXT_INSUFFICIENT_BALANCE, reply_markup=paycat.shop_packages_keyboard())
        if state:
            await state.clear()
        return
    if vr.vip_priority:
        await message.answer(msg.TXT_GEN_STATUS_VIP)
    if state:
        await state.clear()

def _feedback_ticket_header(uid: int, username: str | None) -> str:
    uname = f"@{username}" if username else "без юзернейма"
    return msg.TXT_FEEDBACK_TICKET_HEADER.format(
        username=html.escape(uname),
        user_id=uid,
    )


def support_user_display_name(from_user: object) -> str:
    username = getattr(from_user, "username", None)
    if username:
        return f"@{username}"
    first = (getattr(from_user, "first_name", None) or "").strip()
    last = (getattr(from_user, "last_name", None) or "").strip()
    full = f"{first} {last}".strip()
    return full or "без имени"


def format_support_ticket_admin(uid: int, from_user: object, text: str) -> str:
    """Текст тикета для ADMIN_CHAT_ID (HTML)."""
    return msg.TXT_SUPPORT_TICKET_ADMIN.format(
        user_name=html.escape(support_user_display_name(from_user)),
        user_id=uid,
        text=html.escape(text),
    )


def support_admin_chat_targets() -> list[int]:
    """Чат/чаты для тикетов: ``ADMIN_CHAT_ID`` или список ``admin_ids``."""
    chat_id = int(getattr(settings, "admin_chat_id", 0) or 0)
    if chat_id:
        return [chat_id]
    return [int(x) for x in (settings.admin_ids or []) if int(x)]


async def send_free_create_blocked(target: Message) -> None:
    """HTML-сообщение о блокировке премиум-разделов на тарифе FREE."""
    await target.answer(
        msg.TXT_FREE_CREATE_BLOCKED,
        parse_mode=ParseMode.HTML,
        reply_markup=paycat.shop_packages_keyboard(),
    )


async def guard_free_premium_create(target: Message, user_id: int) -> bool:
    """True — операцию нужно прервать (сообщение уже отправлено)."""
    if await free_blocks_premium_create(user_id):
        await send_free_create_blocked(target)
        return True
    return False


def can_reply_to_support_ticket(message: Message, *, is_admin: bool) -> bool:
    """Ответ админа на тикет: личка или чат ``ADMIN_CHAT_ID``."""
    if not is_admin:
        return False
    if not message.reply_to_message:
        return False
    chat_id = message.chat.id
    targets = support_admin_chat_targets()
    if targets and chat_id in targets:
        return True
    return chat_id == message.from_user.id


def _extract_ticket_user_id(text: str | None) -> int | None:
    if not text:
        return None
    match = _TICKET_USER_ID_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


async def _delete_paywall_messages(target: Message, state: FSMContext | None) -> None:
    if state is None:
        return
    data = await state.get_data()
    raw_ids = data.get("paywall_msg_ids")
    if not isinstance(raw_ids, list):
        return
    for mid in raw_ids:
        try:
            msg_id = int(mid)
        except (TypeError, ValueError):
            continue
        try:
            await target.bot.delete_message(chat_id=target.chat.id, message_id=msg_id)
        except TelegramBadRequest:
            pass
    await state.update_data(paywall_msg_ids=[])


async def send_start_paywall_screen(target: Message, state: FSMContext | None = None) -> None:
    """Hard Paywall на /start: Markdown + кнопки подписки, без главного меню."""
    from aiogram.enums import ParseMode

    from platforms.telegram_keyboards import start_paywall_markup

    m1 = await target.answer(
        msg.format_start_paywall_text(settings),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )
    m2 = await target.answer("👇", reply_markup=start_paywall_markup())
    if state is not None:
        await state.update_data(paywall_msg_ids=[m1.message_id, m2.message_id])


async def send_activation_success(
    target: Message,
    user_id: int,
    *,
    state: FSMContext | None = None,
) -> None:
    """После успешной проверки подписки: убрать заслон и показать главное меню."""
    from aiogram.enums import ParseMode

    from platforms.telegram_keyboards import main_menu

    await _delete_paywall_messages(target, state)
    await target.answer(
        msg.TXT_ACTIVATION_SUCCESS,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(user_id),
    )


async def send_terms_welcome_screen(target: Message) -> None:
    """Алиас для paywall."""
    await send_start_paywall_screen(target)


async def send_terms_required_reminder(target: Message) -> None:
    """Любое сообщение до прохождения paywall."""
    from platforms.telegram_keyboards import start_paywall_markup

    await target.answer(msg.TXT_TERMS_REQUIRED, reply_markup=start_paywall_markup())
