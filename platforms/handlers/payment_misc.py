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


@router.callback_query(F.data.startswith(paycat.CB_PAY_PKG_PREFIX))
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

@router.callback_query(F.data.startswith(paycat.CB_PAY_METHOD_PREFIX))
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

@router.pre_checkout_query()
async def pre_checkout_accept(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)

@router.message(F.successful_payment)
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

@router.callback_query(F.data == msg.CB_RESULT_PREMIUM)
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

@router.callback_query(F.data.in_(result_cbs))
async def result_buttons_stub(callback: CallbackQuery) -> None:
    await callback.answer(msg.TXT_STUB_BUTTON)

@router.callback_query(F.data == msg.CB_SERVICE_RULES)
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

@router.callback_query(F.data == msg.CB_BACK_MAIN)
async def back_main(callback: CallbackQuery) -> None:
    await callback.message.answer(msg.TXT_BACK_TO_MAIN, reply_markup=main_menu(callback.from_user.id))
    await callback.answer()

@router.message(StateFilter(None), F.text.lower().in_(msg.EASTER_THANKS_TRIGGERS))
async def easter_thanks(message: Message) -> None:
    await message.answer(random.choice(msg.EASTER_THANKS_REPLIES))

@router.message(StateFilter(None), HelpInstructionWordFilter())
async def help_instruction_keyword(message: Message) -> None:
    await send_same_as_instruction_button(message)

@router.message(
    StateFilter(None),
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_(_reply_menu_button_texts()),
)
async def chat_handler(message: Message) -> None:
    uid = message.from_user.id
    raw = (message.text or "")[: settings.chat_max_message_chars]
    stream_cb = (
        create_throttled_stream_reply(message, deps.bot(), settings)
        if settings.telegram_chat_streaming
        else None
    )
    async with chat_action_loop(deps.bot(), message.chat.id, "typing"):
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
    if result.outcome is ChatTurnOutcome.ROLE_NOT_ALLOWED:
        await message.answer(msg.TXT_PREMIUM_ROLE_LOCKED, reply_markup=paycat.shop_packages_keyboard())
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

