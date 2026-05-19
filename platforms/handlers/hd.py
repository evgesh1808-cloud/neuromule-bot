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


@router.callback_query(F.data == msg.CB_HD_PREMIUM_BUY)
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
    if crystals < settings.cost_hd:
        await callback.message.answer(
            msg.TXT_HD_INSUFFICIENT_CRYSTALS.format(cost=settings.cost_hd),
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer(
            msg.TXT_HD_INSUFFICIENT_CRYSTALS_ALERT.format(cost=settings.cost_hd),
            show_alert=True,
        )
        return
    await state.set_state(UserFlow.waiting_hd_birth_data)
    await callback.message.answer(
        msg.TXT_HD_ASK_BIRTH_DATA.format(cost=settings.cost_hd),
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
                current_cta_text=get_dynamic_cta_for_today(),
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

@router.message(UserFlow.waiting_advice_birth, Command("cancel"))
async def advice_birth_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(msg.TXT_ADVICE_BIRTH_CANCELLED, parse_mode=ParseMode.HTML)

@router.message(UserFlow.waiting_advice_birth, F.text)
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

@router.callback_query(F.data == msg.CB_HD_FREE_ADVICE)
async def hd_free_advice(callback: CallbackQuery, state: FSMContext) -> None:
    uid = callback.from_user.id
    user = await get_user(uid)
    if (user["last_free_date"] or "") == today_iso():
        await callback.answer(msg.TXT_HD_FREE_ADVICE_USED_ALERT, show_alert=True)
        return
    await callback.answer()
    await _send_daily_advice(callback.message, uid, state)

@router.message(UserFlow.waiting_hd_birth_data, F.text)
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
    spend = await billing.spend_hd_full_report(uid)
    if not spend.ok:
        await message.answer(
            msg.TXT_HD_INSUFFICIENT_CRYSTALS.format(cost=settings.cost_hd),
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        await state.clear()
        return
    charge_id = spend.charge.charge_id if spend.charge else ""

    await message.answer(msg.TXT_HD_PROCESSING, parse_mode=ParseMode.HTML)
    try:
        async with chat_action_loop(deps.bot(), message.chat.id, "typing"):
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
            msg.TXT_HD_PAYMENT_OK.format(cost=settings.cost_hd, balance=row.crystals),
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
        if charge_id:
            await refund_charge(charge_id)
        await message.answer(
            msg.TXT_HD_FAILED,
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    finally:
        await state.clear()

@router.message(UserFlow.waiting_hd_birth_data)
async def hd_premium_need_text(message: Message) -> None:
    await message.answer(msg.TXT_HD_EMPTY_DATA, parse_mode=ParseMode.HTML)

@router.message(UserFlow.WAITING_PARTNER_DATA, F.text)
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
    spend = await billing.spend_hd_match(uid)
    if not spend.ok:
        await message.answer(
            msg.format_match_insufficient_crystals(settings),
            reply_markup=paycat.shop_packages_keyboard(),
        )
        await state.clear()
        return
    match_charge_id = spend.charge.charge_id if spend.charge else ""
    await message.answer(msg.TXT_MATCH_PROCESSING)
    try:
        async with chat_action_loop(deps.bot(), message.chat.id, "typing"):
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
        if match_charge_id:
            await refund_charge(match_charge_id)
        await message.answer(msg.TXT_MATCH_FAILED, reply_markup=paycat.shop_packages_keyboard())
    finally:
        await state.clear()

@router.message(UserFlow.WAITING_PARTNER_DATA)
async def match_need_text(message: Message) -> None:
    await message.answer(msg.TXT_MATCH_EMPTY_DATA)

@router.callback_query(F.data.startswith(msg.CB_HD_REPORT_PREFIX))
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

@router.callback_query(F.data == msg.CB_CABINET_PROMO)
async def cabinet_promo_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserFlow.waiting_promo_code)
    await callback.message.answer(msg.TXT_PROMO_ASK)
    await callback.answer()

@router.callback_query(F.data == msg.CB_SHOW_INSTRUCTION)
async def cabinet_show_instruction(callback: CallbackQuery) -> None:
    await send_same_as_instruction_button(callback.message)
    await callback.answer()

@router.message(UserFlow.waiting_promo_code, F.text)
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

