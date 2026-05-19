"""Утилиты и фильтры Telegram-платформы."""
from __future__ import annotations

import html
import logging
import re

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import settings
from content import messages as msg
from services import payments_catalog as paycat
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
    """Все подписи Reply-кнопок главного меню (для фильтров чата и отмены FSM)."""
    return frozenset({*msg.USER_MAIN_MENU_BUTTONS, msg.ADMIN_MAIN_MENU_BUTTON})


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
