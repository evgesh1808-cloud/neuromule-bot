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
from services.billing.hd_pipeline import spend_hd_advice
from services.god_mode import billing_bypass
from services.billing.pricing import HD_ADVICE_COST
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

def _daily_advice_full_report_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_HD_DAILY_ADVICE_FULL_REPORT_BTN,
                    callback_data=msg.CB_HD_PREMIUM_BUY,
                ),
            ],
        ]
    )


async def _background_advice_worker(
    bot,
    chat_id: int,
    user_profile: dict[str, str],
    *,
    current_cta_text: str,
) -> str:
    """Генерация совета дня с удержанием chat action «typing» на всё время запроса к Gemini."""

    async def _typing_hold_loop() -> None:
        while True:
            try:
                await bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                pass
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(_typing_hold_loop())
    try:
        return await generate_daily_forecast(
            user_profile,
            current_cta_text=current_cta_text,
        )
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass


async def _send_daily_advice(
    target: Message,
    uid: int,
    state: FSMContext | None = None,
    *,
    callback: CallbackQuery | None = None,
) -> None:
    """Пайплайн бесплатного «Совета дня»: лимит → биллинг → lock → Gemini → commit."""
    user = await get_user(uid)
    today = today_iso()

    # Шаг 1: суточный лимит (God Mode — без ограничений)
    if not billing_bypass(uid) and (user["last_free_date"] or "") == today:
        await target.answer(msg.TXT_HD_DAILY_ADVICE_ALREADY_TODAY)
        return

    # Шаг 2: архитектурный биллинг (HD_ADVICE_COST=0 → пропуск)
    spend = await spend_hd_advice(uid)
    if not spend.ok:
        if spend.error == "insufficient_crystals":
            await target.answer(
                msg.TXT_HD_INSUFFICIENT_CRYSTALS.format(cost=HD_ADVICE_COST),
                reply_markup=paycat.shop_packages_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        else:
            await target.answer(msg.TXT_HD_DAILY_ADVICE_GENERATION_FAILED)
        return
    charge_id = spend.charge.charge_id if spend.charge else ""

    # Шаг 3: антиспам-lock
    if not await try_begin_daily_advice(uid):
        if charge_id:
            await refund_charge(charge_id)
        await target.answer(msg.TXT_HD_DAILY_ADVICE_BUSY)
        return

    # Шаг 4: профиль / дата рождения (hd_birth_data → advice_birth_data)
    user_profile = daily_advice_user_profile_from_repo_user(user)
    if user_profile is None:
        if state is not None:
            await state.set_state(UserFlow.waiting_advice_birth)
            await target.answer(msg.TXT_ADVICE_BIRTH_ASK, parse_mode=ParseMode.HTML)
        else:
            await target.answer(msg.TXT_ADVICE_NEED_STATE, parse_mode=ParseMode.HTML)
        await rollback_daily_advice(uid)
        if charge_id:
            await refund_charge(charge_id)
        return

    # Шаг 5: заглушка + снятие часиков на inline-кнопке
    placeholder = await target.answer(msg.TXT_HD_DAILY_ADVICE_CONNECTING)
    if callback is not None:
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass

    cta_text = get_dynamic_cta_for_today()
    try:
        # Шаг 6: фоновая генерация (typing-loop + Gemini stream=False)
        raw_forecast = await _background_advice_worker(
            deps.bot(),
            target.chat.id,
            user_profile,
            current_cta_text=cta_text,
        )
        final_text = sanitize_telegram_plain_text(raw_forecast.strip())
        if not final_text:
            raise RuntimeError("Gemini returned empty daily advice")
        await placeholder.edit_text(
            final_text,
            reply_markup=_daily_advice_full_report_keyboard(),
        )
        await commit_daily_advice(uid)
    except Exception:
        await rollback_daily_advice(uid)
        if charge_id:
            await refund_charge(charge_id)
        logger.exception("hd_free_advice_failed user_id=%s", uid)
        try:
            await placeholder.edit_text(msg.TXT_HD_DAILY_ADVICE_GENERATION_FAILED)
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
    if not billing_bypass(uid) and (user["last_free_date"] or "") == today_iso():
        await callback.answer(msg.TXT_HD_FREE_ADVICE_USED_ALERT, show_alert=True)
        return
    await _send_daily_advice(callback.message, uid, state, callback=callback)

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
        if spend.error == "free_premium_create_blocked":
            from platforms.telegram_utils import send_free_create_blocked

            await send_free_create_blocked(message)
        else:
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
    data = await state.get_data()
    own_birth_data = data.get("match_own_birth_data")
    prefilled_partner = (data.get("match_partner_prefill") or "").strip()

    if prefilled_partner:
        # Family Sharing шорткат: partner_birth_data уже подтянули из карты члена семьи.
        first_birth_data = str(own_birth_data or "").strip()
        second_birth_data = prefilled_partner
    else:
        if not raw:
            await message.answer(msg.TXT_MATCH_EMPTY_DATA)
            return
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
            async with chat_action_loop(deps.bot(), callback.message.chat.id, "upload_document"):
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

