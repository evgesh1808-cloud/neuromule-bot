"""Telegram handlers."""
from __future__ import annotations

import asyncio
import html
import logging
import random
import re
import time
from io import BytesIO
from pathlib import Path

from aiogram import F, Router, types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from config import settings
from content import messages as msg
from content.video_menu import (
    CB_VIDEO_CAT_PREFIX,
    CB_VIDEO_EXTEND,
    CB_VIDEO_LONG,
    CB_VIDEO_PREFIX,
    video_category_menu,
    video_root_menu,
)
from platforms.handlers import deps
from platforms.telegram_keyboards import (
    cabinet_keyboard,
    channel_gate_markup,
    create_menu,
    get_admin_inline_keyboard,
    hd_menu,
    hd_pro_unlocked_keyboard,
    hd_report_sections_markup,
    image_model_menu,
    invite_limit_keyboard,
    main_menu,
    photo_tools_menu,
    service_rules_menu,
    support_faq_keyboard,
    terms_accept_keyboard,
    text_role_menu,
)
from platforms.telegram_states import AdminStates, FeedbackStates, UserFlow
from platforms.telegram_utils import (
    HelpInstructionWordFilter,
    _extract_ticket_user_id,
    _feedback_ticket_header,
    _reply_menu_button_texts,
    _reply_video_gen_result,
    is_admin_user,
    notify_admins_about_payment,
    send_same_as_instruction_button,
)
from services import hd_service
from services import payments_catalog as paycat
from services.billing import billing
from services.billing.store import refund_charge
from services.hd_logic import (
    birth_data_minimum_for_advice,
    change_user_crystals,
    create_pdf,
    daily_advice_user_profile_from_repo_user,
    format_premium_report,
    generate_daily_forecast,
    generate_premium_report,
    get_calculated_gates,
    get_dynamic_cta_for_today,
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
    clear_user_dialog_and_memory,
    commit_daily_advice,
    ensure_user,
    get_sales_stats,
    get_user_row,
    list_all_user_ids,
    rollback_daily_advice,
    sales_stats_as_dict,
    set_user_accepted_terms,
    try_begin_daily_advice,
    update_balance,
)
from services.telegram_safe_text import sanitize_telegram_plain_text
from services.use_cases.animate_generation_turn import AnimateGenOutcome, run_animate_generation_turn
from platforms.telegram_chat_action import chat_action_loop
from platforms.telegram_chat_stream import create_throttled_stream_reply
from platforms.telegram_chunks import answer_chat_text
from services.use_cases.chat_turn import ChatTurnOutcome, run_chat_turn
from services.use_cases.music_generation_turn import MusicGenOutcome, run_music_generation_turn
from services.use_cases.cabinet_turn import build_cabinet_view
from services.use_cases.payment_invoice_turn import InvoiceBuildOutcome, build_payment_invoice_draft
from services.use_cases.payment_shop_turn import build_tariffs_entry_text
from services.use_cases.payment_turn import PaymentApplyOutcome, run_successful_payment_apply
from services.use_cases.photo_generation_turn import PhotoGenOutcome, run_photo_generation_turn
from services.use_cases.promo_turn import PromoOutcome, run_promo_redeem
from services.use_cases.start_turn import StartFlowOutcome, run_start_turn
from services.use_cases.tariff_shop_nav_turn import TariffShopNavOutcome, resolve_tariff_shop_callback
from services.use_cases.video_generation_turn import (
    VideoGenOutcome,
    VideoGenResult,
    classify_scenario_pick,
    run_video_scenario_turn,
)

logger = logging.getLogger(__name__)

router = Router()

is_subscribed = deps.is_subscribed
is_subscribed_cached = deps.is_subscribed_cached
check_and_spend = deps.check_and_spend
send_start_main_welcome = deps.send_start_main_welcome
channel_sub = deps.channel_sub


def _is_admin(user_id: int) -> bool:
    return is_admin_user(user_id)


@router.message(F.text == msg.BTN_DAILY_ADVICE)
async def daily_advice_from_menu(message: Message, state: FSMContext) -> None:
    await _send_daily_advice(message, message.from_user.id, state)

@router.message(F.text == msg.BTN_CREATE)
async def open_create_menu(message: Message) -> None:
    await message.answer(msg.TXT_SELECT_TOOL, reply_markup=create_menu())

@router.message(F.text == msg.BTN_HD_SECTION)
async def open_hd_from_main_menu(message: Message) -> None:
    user = await get_user(message.from_user.id)
    has_pro = bool(user["has_pro_analysis"]) if "has_pro_analysis" in user.keys() else False
    await message.answer(
        msg.TXT_HD_SECTION_INTRO,
        reply_markup=hd_menu(has_pro),
        parse_mode=ParseMode.HTML,
    )

@router.message(F.text == msg.BTN_PROFILE)
async def show_profile_from_short_menu(message: Message) -> None:
    await message.answer(msg.TXT_SECTION_INTRO)
    view = await build_cabinet_view(settings, message.from_user.id)
    await message.answer(view.text, reply_markup=cabinet_keyboard())

@router.message(F.text == msg.BTN_TARIFFS)
async def show_tariffs_from_short_menu(message: Message) -> None:
    await message.answer(msg.TXT_SECTION_INTRO)
    await message.answer(build_tariffs_entry_text(), reply_markup=paycat.shop_packages_keyboard())

@router.message(F.text.in_({msg.BTN_SUPPORT, msg.BTN_SUPPORT_LEGACY}))
async def show_support_and_faq(message: Message) -> None:
    await message.answer(
        msg.format_faq_support_text(settings),
        parse_mode=ParseMode.HTML,
        reply_markup=support_faq_keyboard(),
        link_preview_options=types.LinkPreviewOptions(is_disabled=True),
    )

@router.callback_query(F.data == msg.CB_SUPPORT_WRITE_QUESTION)
async def support_write_question_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(FeedbackStates.waiting_for_user_question)
    await callback.message.answer(msg.TXT_FEEDBACK_ASK)
    await callback.answer()

@router.message(FeedbackStates.waiting_for_user_question, Command("cancel"))
async def support_question_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(msg.TXT_FEEDBACK_CANCELLED, reply_markup=main_menu(message.from_user.id))

@router.message(FeedbackStates.waiting_for_user_question)
async def process_user_question_delivery(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    raw = (message.text or "").strip()
    if raw in _reply_menu_button_texts():
        await state.clear()
        await message.answer(msg.TXT_FEEDBACK_CANCELLED, reply_markup=main_menu(uid))
        return
    if not message.photo and not raw:
        await message.answer(msg.TXT_FEEDBACK_EMPTY)
        return

    admin_ids = list(settings.admin_ids)
    if not admin_ids:
        await state.clear()
        await message.answer(msg.TXT_FEEDBACK_NO_ADMINS)
        return

    username = message.from_user.username
    ticket_header = _feedback_ticket_header(uid, username)
    delivered = 0
    for admin_id in admin_ids:
        try:
            if message.photo:
                body = (message.caption or "").strip()
                caption = f"{ticket_header}{html.escape(body)}" if body else ticket_header.rstrip()
                await message.bot.send_photo(
                    chat_id=admin_id,
                    photo=message.photo[-1].file_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
            else:
                await message.bot.send_message(
                    chat_id=admin_id,
                    text=f"{ticket_header}{html.escape(raw)}",
                    parse_mode=ParseMode.HTML,
                )
            delivered += 1
        except Exception:
            logger.exception("feedback_forward_failed admin_id=%s user_id=%s", admin_id, uid)

    await state.clear()
    if delivered:
        await message.answer(msg.TXT_FEEDBACK_DELIVERED)
    else:
        await message.answer(msg.TXT_FEEDBACK_NO_ADMINS)

@router.message(F.reply_to_message, F.text)
async def admin_reply_to_user_process(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    reply_to = message.reply_to_message
    if reply_to is None:
        return
    text_to_search = reply_to.text or reply_to.caption
    target_uid = _extract_ticket_user_id(text_to_search)
    if target_uid is None:
        return
    reply_body = html.escape((message.text or "").strip())
    if not reply_body:
        return
    try:
        await message.bot.send_message(
            chat_id=target_uid,
            text=msg.TXT_FEEDBACK_REPLY_USER.format(body=reply_body),
            parse_mode=ParseMode.HTML,
        )
        await message.reply(
            msg.TXT_FEEDBACK_REPLY_SENT.format(user_id=target_uid),
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logger.exception("feedback_admin_reply_failed target_uid=%s", target_uid)
        await message.reply(msg.TXT_FEEDBACK_REPLY_FAILED.format(error=html.escape(str(exc))))

