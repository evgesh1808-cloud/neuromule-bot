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


@router.message(UserFlow.waiting_for_text_prompt, F.text)
async def text_role_process(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    role_id = str(data.get("text_role") or "standard")
    uid = message.from_user.id
    raw = (message.text or "")[: settings.chat_max_message_chars]

    async with chat_action_loop(deps.bot(), message.chat.id, "typing"):
        await message.answer("Прокладываю кратчайший путь через нейроны...")
        stream_cb = (
            create_throttled_stream_reply(message, deps.bot(), settings)
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

@router.message(UserFlow.waiting_for_text_prompt)
async def text_role_need_text(message: Message) -> None:
    await message.answer(msg.TXT_CREATE_TEXT_HINT, reply_markup=text_role_menu())

@router.message(UserFlow.waiting_for_photo, F.text)
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

@router.message(UserFlow.waiting_for_photo)
async def photo_process_need_text(message: Message) -> None:
    await message.answer(msg.TXT_CREATE_IMAGE_AFTER_MODEL)

@router.message(UserFlow.waiting_for_video, F.text)
async def video_process(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    prompt = (message.text or "").strip()
    data = await state.get_data()
    scenario_id = data.get("video_scenario_id") or "video_pro_5sec"
    vr = await run_video_scenario_turn(
        settings,
        bot,
        message.chat.id,
        user_id,
        str(scenario_id),
        user_prompt=prompt,
    )
    await _reply_video_gen_result(message, vr, state)

@router.message(UserFlow.waiting_for_video_prank_photo, F.photo)
async def video_prank_photo_process(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    data = await state.get_data()
    scenario_id = str(data.get("video_scenario_id") or "")
    caption = (message.caption or "").strip()
    vr = await run_video_scenario_turn(
        settings,
        bot,
        message.chat.id,
        uid,
        scenario_id,
        user_prompt=caption,
        telegram_file_id=message.photo[-1].file_id,
    )
    await _reply_video_gen_result(message, vr, state)

@router.message(UserFlow.waiting_for_video)
async def video_need_text(message: Message) -> None:
    await message.answer(msg.TXT_VIDEO_NEED_PROMPT)

@router.message(UserFlow.waiting_for_video_prank_photo)
async def video_prank_need_photo(message: Message) -> None:
    await message.answer(msg.TXT_VIDEO_NEED_PHOTO, parse_mode=ParseMode.HTML)

@router.message(UserFlow.waiting_for_animate, F.photo)
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

@router.message(UserFlow.waiting_for_animate)
async def animate_need_photo(message: Message) -> None:
    await message.answer(msg.TXT_CREATE_ANIMATE_HINT)

@router.message(UserFlow.waiting_for_upscale_photo, F.photo)
async def upscale_process(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    spend = await billing.spend_upscale(uid)
    if not spend.ok:
        await message.answer(
            msg.TXT_INSUFFICIENT_BALANCE,
            reply_markup=paycat.shop_packages_keyboard(),
        )
        await state.clear()
        return
    upscale_charge_id = spend.charge.charge_id if spend.charge else ""
    await message.answer(msg.TXT_UPSCALE_PROCESSING)
    try:
        async with chat_action_loop(deps.bot(), message.chat.id, "upload_document"):
            row = await get_user_row(uid)
            photo_id = message.photo[-1].file_id
            file = await deps.bot().get_file(photo_id)
            if not file.file_path:
                raise RuntimeError("Telegram did not return file_path for upscale photo")
            buffer = BytesIO()
            await deps.bot().download_file(file.file_path, buffer)
            document = BufferedInputFile(buffer.getvalue(), filename="neuromule_upscale.jpg")
            await deps.bot().send_document(
                message.chat.id,
                document,
                caption=msg.TXT_UPSCALE_SUCCESS.format(balance=row.crystals),
            )
    except Exception:
        logger.exception("upscale_failed user_id=%s", uid)
        if upscale_charge_id:
            await refund_charge(upscale_charge_id)
        await message.answer(msg.TXT_UPSCALE_FAILED)
    finally:
        await state.clear()

@router.message(UserFlow.waiting_for_upscale_photo)
async def upscale_need_photo(message: Message) -> None:
    await message.answer(msg.TXT_UPSCALE_HINT)

@router.message(UserFlow.waiting_for_music, F.text)
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

@router.message(UserFlow.waiting_for_music)
async def music_need_text(message: Message) -> None:
    await message.answer(msg.TXT_CREATE_MUSIC_HINT)

@router.message(Command("profile"))
async def profile(message: Message) -> None:
    view = await build_cabinet_view(settings, message.from_user.id)
    await message.answer(view.text, reply_markup=cabinet_keyboard())

