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
from platforms.handlers.hd import _send_daily_advice
from content.video_menu import video_root_menu
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
)
from platforms.telegram_states import AdminStates, FeedbackStates, UserFlow
from platforms.telegram_utils import (
    HelpInstructionWordFilter,
    _extract_ticket_user_id,
    _reply_menu_button_texts,
    _reply_video_gen_result,
    is_admin_user,
    notify_admins_about_payment,
    guard_free_premium_create,
    send_same_as_instruction_button,
    can_reply_to_support_ticket,
    format_support_ticket_admin,
    support_admin_chat_targets,
)
from platforms.support_center import (
    FAQ_ANSWER_BY_CALLBACK,
    edit_support_screen,
    support_back_main_keyboard,
    support_faq_back_keyboard,
    support_faq_menu_keyboard,
    support_guides_text,
    support_main_keyboard,
    support_main_text,
    support_manage_subscription_text,
    support_payment_keyboard,
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
from platforms.neurotext_flow import send_neurotext_role_menu
from platforms.tariffs_center import send_tariffs_screen
from services.use_cases.payment_shop_turn import build_tariffs_entry_text
from services.use_cases.payment_turn import PaymentApplyOutcome, run_successful_payment_apply
from services.use_cases.photo_generation_turn import PhotoGenOutcome, run_photo_generation_turn
from platforms.handlers.promo_input import handle_promo_code_message
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
async def open_create_inline_menu(message: Message) -> None:
    """Главная кнопка «🎨 Создать» → inline-сетка 2×3 (``create_menu``).

    Раньше здесь открывалось вертикальное Reply-подменю ``create_reply_menu`` —
    из-за этого новая симметричная inline-сетка не была видна при обычном
    сценарии из главного меню.
    """
    await message.answer(msg.TXT_SELECT_TOOL, reply_markup=create_menu())


async def _open_hd_section(message: Message) -> None:
    uid = message.from_user.id
    if await guard_free_premium_create(message, uid):
        return
    user = await get_user(uid)
    has_pro = bool(user["has_pro_analysis"]) if "has_pro_analysis" in user.keys() else False
    await message.answer(
        msg.TXT_HD_SECTION_INTRO,
        reply_markup=hd_menu(has_pro),
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text == msg.BTN_REPLY_HD)
async def open_hd_from_create_menu(message: Message) -> None:
    await _open_hd_section(message)


@router.message(F.text == msg.BTN_HD_SECTION)
async def open_hd_legacy_label(message: Message) -> None:
    await _open_hd_section(message)


@router.message(F.text.in_({msg.BTN_REPLY_NEUROTEXT, msg.BTN_REPLY_NEUROTEXT_LEGACY}))
async def reply_create_neurotext(message: Message, state: FSMContext) -> None:
    await send_neurotext_role_menu(message, state)


@router.message(F.text == msg.BTN_REPLY_IMAGE)
async def reply_create_image(message: Message) -> None:
    from services.billing.types import TariffTier
    from services.repository import get_user_row

    row = await get_user_row(message.from_user.id)
    tariff = TariffTier.from_db(row.tariff)
    text = msg.get_text_image_models(tariff)
    await message.answer(
        text,
        reply_markup=image_model_menu(
            tariff,
            photo_daily_count=row.photo_daily_count,
            photo_daily_date=row.photo_daily_date,
        ),
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text == msg.BTN_REPLY_ANIMATE)
async def reply_create_animate(message: Message, state: FSMContext) -> None:
    if await guard_free_premium_create(message, message.from_user.id):
        return
    await message.answer(msg.TXT_CREATE_ANIMATE_HINT)
    await state.set_state(UserFlow.waiting_for_animate)


@router.message(F.text == msg.BTN_REPLY_VIDEO)
async def reply_create_video(message: Message, state: FSMContext) -> None:
    if await guard_free_premium_create(message, message.from_user.id):
        return
    await state.clear()
    await message.answer(
        msg.TXT_CREATE_VIDEO_HINT,
        reply_markup=video_root_menu(),
        parse_mode=ParseMode.HTML,
    )

async def _is_duo_owner_user(user_id: int) -> bool:
    """True для владельца DUO (ULTRA 1 месяц) — кнопка «Управление DUO-доступом»."""
    from services.family_sharing import is_duo_owner_eligible

    try:
        return await is_duo_owner_eligible(user_id)
    except Exception:
        logger.exception("is_duo_owner_user: failed uid=%s", user_id)
        return False


async def _send_profile_screen(target: Message, user_id: int) -> None:
    view = await build_cabinet_view(settings, user_id)
    is_duo = await _is_duo_owner_user(user_id)
    await target.answer(
        view.text,
        reply_markup=cabinet_keyboard(is_duo_owner=is_duo),
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text.in_(msg.PROFILE_MENU_BUTTONS))
async def show_profile_from_short_menu(message: Message) -> None:
    await _send_profile_screen(message, message.from_user.id)


@router.callback_query(F.data == msg.CB_REFRESH_PROFILE)
async def refresh_profile_balance(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer()
        return
    view = await build_cabinet_view(settings, callback.from_user.id)
    is_duo = await _is_duo_owner_user(callback.from_user.id)
    try:
        await callback.message.edit_text(
            view.text,
            reply_markup=cabinet_keyboard(is_duo_owner=is_duo),
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            await callback.answer(msg.TXT_PROFILE_ALREADY_FRESH, show_alert=False)
            return
        await callback.message.answer(
            view.text,
            reply_markup=cabinet_keyboard(is_duo_owner=is_duo),
            parse_mode=ParseMode.HTML,
        )
    await callback.answer(msg.TXT_PROFILE_REFRESH_OK)


@router.callback_query(F.data.in_({msg.CB_ENTER_PROMOCODE, msg.CB_CABINET_PROMO}))
async def profile_enter_promocode(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserFlow.waiting_promo_code)
    if callback.message:
        await callback.message.answer(msg.TXT_PROMO_ASK)
    await callback.answer()

@router.message(F.text == msg.BTN_TARIFFS)
async def show_tariffs_from_short_menu(message: Message) -> None:
    await send_tariffs_screen(message, build_tariffs_entry_text())


@router.message(UserFlow.waiting_promo_code, F.text)
async def promo_redeem(message: Message, state: FSMContext) -> None:
    await handle_promo_code_message(message, state)

@router.message(
    F.text.in_({msg.BTN_SUPPORT, msg.BTN_SUPPORT_LEGACY, msg.BTN_SUPPORT_LEGACY2})
)
async def show_support_and_faq(message: Message) -> None:
    await message.answer(
        msg.format_support_text(settings),
        parse_mode=ParseMode.HTML,
        reply_markup=support_faq_keyboard(),
        link_preview_options=types.LinkPreviewOptions(is_disabled=True),
    )

@router.callback_query(F.data == msg.CB_BACK_TO_SUPP_MAIN)
async def support_back_to_main(callback: CallbackQuery) -> None:
    await edit_support_screen(callback, support_main_text(), support_main_keyboard())


@router.callback_query(F.data == msg.CB_SUPP_FAQ)
async def support_show_faq_menu(callback: CallbackQuery) -> None:
    await edit_support_screen(
        callback,
        msg.TXT_SUPPORT_FAQ_MENU,
        support_faq_menu_keyboard(),
    )


@router.callback_query(F.data.in_(set(FAQ_ANSWER_BY_CALLBACK.keys())))
async def support_show_faq_answer(callback: CallbackQuery) -> None:
    text = FAQ_ANSWER_BY_CALLBACK.get(callback.data or "")
    if not text:
        await callback.answer()
        return
    await edit_support_screen(callback, text, support_faq_back_keyboard())


@router.callback_query(F.data == msg.CB_SUPP_GUIDES)
async def support_show_guides(callback: CallbackQuery) -> None:
    await edit_support_screen(
        callback,
        support_guides_text(),
        support_back_main_keyboard(),
    )


@router.callback_query(F.data == msg.CB_SUPP_PAYMENT_ISSUE)
async def support_payment_issue(callback: CallbackQuery) -> None:
    await edit_support_screen(
        callback,
        msg.TXT_SUPPORT_PAYMENT_ISSUE,
        support_payment_keyboard(),
    )


@router.callback_query(F.data == msg.CB_CHECK_PENDING_PAYMENT)
async def support_check_pending_payment(callback: CallbackQuery) -> None:
    uid = callback.from_user.id
    row = await get_user_row(uid)
    await callback.answer(
        f"⚡️ {row.energy} | 💎 {row.crystals_balance} | тариф: {row.tariff}",
        show_alert=True,
    )


@router.callback_query(F.data == msg.CB_MANAGE_SUBSCRIPTION)
async def support_manage_subscription(callback: CallbackQuery) -> None:
    await edit_support_screen(
        callback,
        support_manage_subscription_text(),
        support_back_main_keyboard(),
    )


@router.callback_query(F.data == msg.CB_CLOSE_SUPPORT)
async def support_close(callback: CallbackQuery) -> None:
    if callback.message:
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass
    await callback.answer()


@router.callback_query(
    F.data.in_({msg.CB_WRITE_TO_MANAGER, msg.CB_SUPPORT_WRITE_QUESTION})
)
async def support_write_to_manager(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(FeedbackStates.waiting_support_message)
    if callback.message:
        await callback.message.answer(
            msg.TXT_SUPPORT_WRITE_ASK,
            parse_mode=ParseMode.HTML,
        )
    await callback.answer()


@router.message(FeedbackStates.waiting_support_message, Command("cancel"))
async def support_question_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(msg.TXT_FEEDBACK_CANCELLED, reply_markup=main_menu(message.from_user.id))

@router.message(FeedbackStates.waiting_support_message)
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

    targets = support_admin_chat_targets()
    if not targets:
        await state.clear()
        await message.answer(msg.TXT_FEEDBACK_NO_ADMINS)
        return

    body_text = raw or (message.caption or "").strip() or "— (без текста, только вложение)"
    ticket_text = format_support_ticket_admin(uid, message.from_user, body_text)
    delivered = 0
    for chat_id in targets:
        try:
            if message.photo:
                await message.bot.send_photo(
                    chat_id=chat_id,
                    photo=message.photo[-1].file_id,
                    caption=ticket_text,
                    parse_mode=ParseMode.HTML,
                )
            else:
                await message.bot.send_message(
                    chat_id=chat_id,
                    text=ticket_text,
                    parse_mode=ParseMode.HTML,
                )
            delivered += 1
        except Exception:
            logger.exception(
                "support_ticket_forward_failed chat_id=%s user_id=%s", chat_id, uid
            )

    await state.clear()
    if delivered:
        await message.answer(msg.TXT_SUPPORT_TICKET_OK, parse_mode=ParseMode.HTML)
    else:
        await message.answer(msg.TXT_FEEDBACK_NO_ADMINS)

@router.message(F.reply_to_message, F.text, F.from_user.id.in_(settings.admin_ids))
async def admin_reply_to_user_process(message: Message) -> None:
    if not can_reply_to_support_ticket(
        message, is_admin=_is_admin(message.from_user.id)
    ):
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
            text=msg.TXT_SUPPORT_REPLY_USER.format(body=reply_body),
            parse_mode=ParseMode.HTML,
        )
        await message.reply(
            msg.TXT_FEEDBACK_REPLY_SENT.format(user_id=target_uid),
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logger.exception("feedback_admin_reply_failed target_uid=%s", target_uid)
        await message.reply(msg.TXT_FEEDBACK_REPLY_FAILED.format(error=html.escape(str(exc))))

