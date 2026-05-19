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


@router.message(Command("start"))
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id
    uname = message.from_user.username if message.from_user else None
    result = await run_start_turn(
        settings,
        uid,
        uname,
        message.text,
        is_subscribed=is_subscribed,
    )
    if result.outcome is StartFlowOutcome.NEED_TERMS:
        await state.update_data(pending_start_text=message.text or "")
        await message.answer(
            msg.TXT_TERMS_WELCOME,
            reply_markup=terms_accept_keyboard(),
        )
        return
    if result.outcome is StartFlowOutcome.NEED_CHANNEL:
        await message.answer(msg.TXT_CHANNEL_GATE, reply_markup=channel_gate_markup())
        return
    await send_start_main_welcome(message, uid)

@router.callback_query(F.data == msg.CB_ACCEPT_RULES)
async def accept_rules(callback: CallbackQuery, state: FSMContext) -> None:
    uid = callback.from_user.id
    await set_user_accepted_terms(uid, accepted=True)
    fsm_data = await state.get_data()
    pending_start = fsm_data.get("pending_start_text")
    await state.clear()
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass
    except Exception:
        logger.debug("accept_rules_delete_message_failed", exc_info=True)
    result = await run_start_turn(
        settings,
        uid,
        callback.from_user.username,
        pending_start if isinstance(pending_start, str) else None,
        is_subscribed=is_subscribed,
    )
    if result.outcome is StartFlowOutcome.NEED_CHANNEL:
        await callback.message.answer(msg.TXT_CHANNEL_GATE, reply_markup=channel_gate_markup())
        await callback.answer()
        return
    await send_start_main_welcome(callback.message, uid)
    await callback.answer()

@router.callback_query(F.data == msg.CB_CHECK_SUBSCRIPTION)
async def check_subscription(callback: CallbackQuery) -> None:
    uid = callback.from_user.id
    channel_sub().invalidate(uid)
    if await is_subscribed_cached(uid):
        await callback.message.answer(
            msg.TXT_CHANNEL_GATE_OK,
            reply_markup=main_menu(uid),
        )
        await callback.answer()
        return
    await callback.answer(msg.TXT_CHANNEL_GATE_FAIL, show_alert=True)

@router.message(Command("reset"))
async def cmd_reset_dialog(message: Message, state: FSMContext) -> None:
    await state.clear()
    await clear_user_dialog_and_memory(message.from_user.id)
    await message.answer(msg.TXT_RESET_OK)

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await send_same_as_instruction_button(message)

async def start_match_flow(target: Message, user_id: int, state: FSMContext) -> None:
    user = await get_user(user_id)
    has_pro = bool(user["has_pro_analysis"]) if "has_pro_analysis" in user.keys() else False
    if not has_pro:
        await target.answer(msg.TXT_MATCH_LOCKED, reply_markup=hd_menu(False))
        return
    crystals = int(user["crystals"] or 0)
    if crystals < settings.cost_match:
        await target.answer(
            msg.format_match_insufficient_crystals(settings),
            reply_markup=paycat.shop_packages_keyboard(),
        )
        return
    own_birth_data = (user["hd_birth_data"] or "").strip() if "hd_birth_data" in user.keys() else ""
    await state.update_data(match_own_birth_data=own_birth_data or None)
    await state.set_state(UserFlow.WAITING_PARTNER_DATA)
    if own_birth_data:
        await target.answer(msg.format_match_ask_second(settings))
    else:
        await target.answer(msg.format_match_ask_both(settings))

@router.message(Command("match"))
async def cmd_match(message: Message, state: FSMContext) -> None:
    await start_match_flow(message, message.from_user.id, state)

@router.message(Command("admin"))
@router.message(F.text == msg.ADMIN_MAIN_MENU_BUTTON)
async def show_admin_panel(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    if not _is_admin(uid):
        await message.answer(msg.TXT_ADMIN_DENIED)
        return
    await state.clear()
    logger.info("admin_panel_open user_id=%s", uid)
    await message.answer(msg.TXT_ADMIN_PANEL, reply_markup=get_admin_inline_keyboard())

@router.message(Command("debug_pay"))
async def admin_debug_pay(message: Message) -> None:
    uid = message.from_user.id
    if not _is_admin(uid):
        await message.answer(msg.TXT_ADMIN_DENIED)
        return
    await update_balance(uid, "crystals", 100)
    row = await get_user_row(uid)
    logger.info("admin_debug_pay user_id=%s", uid)
    await message.answer(f"Тестовое пополнение выполнено: +100 💎. Баланс: {row.crystals} 💎")

@router.message(Command("give_energy"))
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

@router.message(Command("add_promo"))
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

@router.callback_query(F.data == msg.CB_ADMIN_STATS)
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

@router.callback_query(
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

@router.message(AdminStates.waiting_for_crystals, F.text)
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

@router.callback_query(
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

@router.message(AdminStates.waiting_for_broadcast)
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

