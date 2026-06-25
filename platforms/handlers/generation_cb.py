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
    CB_VIDEO_REGENERATE,
    CB_VIDEO_UPSCALE,
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


from platforms.handlers.start_admin import start_match_flow
from platforms.neurotext_flow import (
    handle_back_to_roles_menu,
    handle_clear_context,
    handle_neurotext_role_pick,
    handle_show_lifestyle_subcategories,
    handle_show_table_subcategories,
    open_neurotext_from_callback,
    send_neurotext_role_menu,
)

@router.callback_query(F.data == msg.CB_BACK_CREATE)
async def back_create(callback: CallbackQuery) -> None:
    await callback.message.answer(msg.TXT_SELECT_TOOL, reply_markup=create_menu())
    await callback.answer()


@router.callback_query(F.data == msg.CB_BACK_TO_TOOLS)
async def back_to_tools(callback: CallbackQuery) -> None:
    await callback.message.answer(msg.TXT_SELECT_TOOL, reply_markup=create_menu())
    await callback.answer()

@router.callback_query(F.data == msg.CB_HD_SECTION)
async def open_hd_section(callback: CallbackQuery) -> None:
    user = await get_user(callback.from_user.id)
    has_pro = bool(user["has_pro_analysis"]) if "has_pro_analysis" in user.keys() else False
    await callback.message.answer(
        msg.TXT_HD_SECTION_INTRO,
        reply_markup=hd_menu(has_pro),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()

@router.callback_query(F.data == msg.CB_HD_REPORT_OPEN)
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

@router.callback_query(F.data == msg.CB_CREATE_TEXT)
async def create_text_hint(callback: CallbackQuery, state: FSMContext) -> None:
    await open_neurotext_from_callback(callback, state)


@router.callback_query(F.data.startswith(msg.CB_TEXT_ROLE_PREFIX))
async def pick_text_role(callback: CallbackQuery, state: FSMContext) -> None:
    await handle_neurotext_role_pick(
        callback,
        state,
        tariffs_keyboard=paycat.shop_packages_keyboard,
    )


@router.callback_query(F.data.startswith(msg.CB_SET_ROLE_PREFIX))
async def pick_set_role(callback: CallbackQuery, state: FSMContext) -> None:
    await handle_neurotext_role_pick(
        callback,
        state,
        tariffs_keyboard=paycat.shop_packages_keyboard,
    )


@router.callback_query(F.data == msg.CB_SHOW_TABLE_SUBCATEGORIES)
async def show_table_subcategories(callback: CallbackQuery, state: FSMContext) -> None:
    await handle_show_table_subcategories(
        callback,
        state,
        tariffs_keyboard=paycat.shop_packages_keyboard,
    )


@router.callback_query(F.data == msg.CB_SHOW_LIFESTYLE_SUBCATEGORIES)
async def show_lifestyle_subcategories(callback: CallbackQuery, state: FSMContext) -> None:
    await handle_show_lifestyle_subcategories(callback, state)


@router.callback_query(F.data == msg.CB_BACK_TO_ROLES_MENU)
async def back_to_roles_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await handle_back_to_roles_menu(callback, state)


@router.callback_query(F.data.startswith(msg.CB_AUDIT_PLATFORM_PREFIX))
async def pick_audit_platform(callback: CallbackQuery, state: FSMContext) -> None:
    """Выбор площадки → FSM ожидания файла и инструкция загрузки."""
    from platforms.marketplace_audit_flow import activate_marketplace_audit
    from services.marketplace_platform import VALID_MARKETPLACE_PLATFORMS

    platform_raw = (callback.data or "").removeprefix(msg.CB_AUDIT_PLATFORM_PREFIX).strip().lower()
    if platform_raw not in VALID_MARKETPLACE_PLATFORMS:
        await callback.answer("Неизвестная площадка.", show_alert=True)
        return

    await callback.answer()
    await activate_marketplace_audit(state, platform=platform_raw)
    instruction = msg.audit_platform_upload_instruction(platform_raw)

    if callback.message:
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
        reply = await callback.message.answer(instruction, parse_mode=ParseMode.HTML)
        await state.update_data(audit_upload_prompt_message_id=reply.message_id)


@router.callback_query(F.data.startswith(msg.CB_TABLE_SUBROLE_PREFIX))
async def pick_table_subrole(callback: CallbackQuery, state: FSMContext) -> None:
    """Выбор под-режима table_generator → инструкция и ожидание файла/текста."""
    subrole_id = (callback.data or "").removeprefix(msg.CB_TABLE_SUBROLE_PREFIX).strip().lower()

    if subrole_id == "__menu__":
        from platforms.neurotext_flow import handle_show_table_subrole_menu

        await handle_show_table_subrole_menu(callback, state)
        return

    await callback.answer()
    from services.table_subrole_types import VALID_TABLE_SUBROLES, normalize_table_subrole

    if subrole_id not in VALID_TABLE_SUBROLES:
        await callback.answer("Неизвестный режим таблиц.", show_alert=True)
        return

    normalized = normalize_table_subrole(subrole_id)
    await state.update_data(
        text_role="table_generator",
        table_subrole=normalized,
        audit_platform=None,
    )
    await state.set_state(UserFlow.waiting_for_text_prompt)

    instruction = msg.table_subrole_instruction(normalized)
    if callback.message:
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
        await callback.message.answer(
            instruction,
            parse_mode=ParseMode.HTML,
        )


@router.callback_query(F.data == msg.CB_CLEAR_CONTEXT)
async def neurotext_clear_context(callback: CallbackQuery, state: FSMContext) -> None:
    await handle_clear_context(callback, state)


@router.callback_query(F.data == msg.CB_NEW_DIALOG)
async def neurotext_new_dialog(callback: CallbackQuery, state: FSMContext) -> None:
    await handle_clear_context(callback, state)

@router.callback_query(F.data == msg.CB_CREATE_IMAGE)
async def create_image_menu(callback: CallbackQuery) -> None:
    from services.billing.types import TariffTier
    from services.repository import get_user_row

    row = await get_user_row(callback.from_user.id)
    tariff = TariffTier.from_db(row.tariff)
    text = msg.get_text_image_models(tariff)
    await callback.message.answer(
        text,
        reply_markup=image_model_menu(
            tariff,
            photo_daily_count=row.photo_daily_count,
            photo_daily_date=row.photo_daily_date,
        ),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()

@router.callback_query(F.data == msg.CB_UPSCALE_START)
async def upscale_start_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserFlow.waiting_for_upscale_photo)
    await callback.message.answer(msg.TXT_UPSCALE_HINT)
    await callback.answer()

@router.callback_query(F.data.startswith(msg.CB_IMG_PREFIX))
async def pick_image_model(callback: CallbackQuery, state: FSMContext) -> None:
    from services.billing.free_tier_gates import free_allows_image_model, is_free_user

    mid = callback.data[len(msg.CB_IMG_PREFIX) :]
    if await is_free_user(callback.from_user.id) and not free_allows_image_model(mid):
        await callback.answer("Доступно только Imagen 4 и Flux", show_alert=True)
        if callback.message:
            await callback.message.answer(
                msg.TXT_FREE_IMAGE_MODEL_BLOCKED,
                parse_mode=ParseMode.HTML,
            )
        return
    if mid not in msg.IMAGE_MODEL_IDS:
        await callback.answer(msg.TXT_UNKNOWN_IMAGE_MODEL, show_alert=True)
        return
    label = next(lbl for lbl, i in msg.IMAGE_MODELS if i == mid)
    await state.update_data(image_model_id=mid, image_model_label=label)
    await state.set_state(UserFlow.waiting_for_photo)
    await callback.message.answer(msg.TXT_CREATE_IMAGE_AFTER_MODEL)
    await callback.answer()

@router.callback_query(F.data == msg.CB_CREATE_ANIMATE)
async def create_animate_start(callback: CallbackQuery, state: FSMContext) -> None:
    from platforms.telegram_utils import guard_free_premium_create

    if callback.message and await guard_free_premium_create(callback.message, callback.from_user.id):
        await callback.answer()
        return
    await callback.message.answer(msg.TXT_CREATE_ANIMATE_HINT)
    await state.set_state(UserFlow.waiting_for_animate)
    await callback.answer()

@router.callback_query(F.data == msg.CB_CREATE_VIDEO)
async def create_video_start(callback: CallbackQuery, state: FSMContext) -> None:
    from platforms.telegram_utils import guard_free_premium_create

    if callback.message and await guard_free_premium_create(callback.message, callback.from_user.id):
        await callback.answer()
        return
    await state.clear()
    await callback.message.answer(
        msg.TXT_CREATE_VIDEO_HINT,
        reply_markup=video_root_menu(),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()

@router.callback_query(F.data.startswith(CB_VIDEO_CAT_PREFIX))
async def video_pick_category(callback: CallbackQuery) -> None:
    cat = (callback.data or "")[len(CB_VIDEO_CAT_PREFIX) :]
    await callback.message.answer(
        "Выбери сценарий:",
        reply_markup=video_category_menu(cat),
    )
    await callback.answer()

@router.callback_query(
    F.data.startswith(CB_VIDEO_PREFIX)
    & ~F.data.in_({CB_VIDEO_EXTEND, CB_VIDEO_LONG, CB_VIDEO_REGENERATE, CB_VIDEO_UPSCALE})
)
async def video_pick_scenario(callback: CallbackQuery, state: FSMContext) -> None:
    scenario_id = (callback.data or "")[len(CB_VIDEO_PREFIX) :]
    uid = callback.from_user.id
    pre = classify_scenario_pick(scenario_id)
    if pre is VideoGenOutcome.NEED_PHOTO:
        await state.set_state(UserFlow.waiting_for_video_prank_photo)
        await state.update_data(video_scenario_id=scenario_id)
        await callback.message.answer(msg.TXT_VIDEO_NEED_PHOTO, parse_mode=ParseMode.HTML)
        await callback.answer()
        return
    if pre is VideoGenOutcome.NEED_PROMPT:
        await state.set_state(UserFlow.waiting_for_video)
        await state.update_data(video_scenario_id=scenario_id)
        await callback.message.answer(msg.TXT_VIDEO_NEED_PROMPT)
        await callback.answer()
        return
    vr = await run_video_scenario_turn(settings, bot, callback.message.chat.id, uid, scenario_id)
    await _reply_video_gen_result(callback.message, vr, state)
    await callback.answer()

@router.callback_query(F.data == CB_VIDEO_EXTEND)
async def video_extend_callback(callback: CallbackQuery) -> None:
    uid = callback.from_user.id
    vr = await run_video_scenario_turn(
        settings, bot, callback.message.chat.id, uid, "video_extend_5sec"
    )
    await callback.answer(
        msg.TXT_VIDEO_EXTEND_OK if vr.outcome is VideoGenOutcome.SUCCESS else "Ошибка",
        show_alert=vr.outcome is not VideoGenOutcome.SUCCESS,
    )

@router.callback_query(F.data == CB_VIDEO_LONG)
async def video_long_callback(callback: CallbackQuery) -> None:
    uid = callback.from_user.id
    vr = await run_video_scenario_turn(
        settings, bot, callback.message.chat.id, uid, "video_long_pro"
    )
    await callback.answer(
        msg.TXT_VIDEO_LONG_OK if vr.outcome is VideoGenOutcome.SUCCESS else "Ошибка",
        show_alert=vr.outcome is not VideoGenOutcome.SUCCESS,
    )


@router.callback_query(F.data == CB_VIDEO_REGENERATE)
async def video_regenerate_callback(callback: CallbackQuery) -> None:
    """Повторная генерация последнего видео-сценария пользователя."""
    from services.last_video_request import get as get_last_video

    uid = callback.from_user.id
    await callback.answer()
    last = get_last_video(uid)
    if not last:
        await callback.answer(msg.TXT_VIDEO_REGENERATE_NO_HISTORY, show_alert=True)
        return
    vr = await run_video_scenario_turn(
        settings,
        bot,
        callback.message.chat.id,
        uid,
        last.scenario_id,
        user_prompt=last.prompt,
        telegram_file_id=last.file_id or "",
    )
    if vr.outcome is not VideoGenOutcome.SUCCESS:
        await callback.message.answer(
            msg.TXT_VIDEO_REGENERATE_FAILED, parse_mode=ParseMode.HTML
        )


@router.callback_query(F.data == CB_VIDEO_UPSCALE)
async def video_upscale_callback(callback: CallbackQuery) -> None:
    """Видео-апскейл (5 💎). Пока — апсейл-плейсхолдер без списания."""
    await callback.answer(msg.TXT_VIDEO_UPSCALE_SOON, show_alert=True)

@router.callback_query(F.data == msg.CB_MATCH_START)
async def match_start(callback: CallbackQuery, state: FSMContext) -> None:
    await start_match_flow(callback.message, callback.from_user.id, state)
    await callback.answer()


@router.callback_query(F.data == msg.CB_HD_MATCH_MANUAL)
async def match_manual_input(callback: CallbackQuery, state: FSMContext) -> None:
    """Сброс на ручной ввод данных партнёра (FSM WAITING_PARTNER_DATA)."""
    await callback.answer()
    data = await state.get_data()
    own = data.get("match_own_birth_data")
    await state.set_state(UserFlow.WAITING_PARTNER_DATA)
    if own:
        await callback.message.answer(msg.format_match_ask_second(settings))
    else:
        await callback.message.answer(msg.format_match_ask_both(settings))


@router.callback_query(F.data.startswith(msg.CB_HD_MATCH_FAMILY_PREFIX))
async def match_pick_family(callback: CallbackQuery, state: FSMContext) -> None:
    """Шорткат: берёт hd_birth_data выбранного member и переводит в FSM с готовым вводом."""
    await callback.answer()
    raw = (callback.data or "").removeprefix(msg.CB_HD_MATCH_FAMILY_PREFIX)
    try:
        member_id = int(raw)
    except ValueError:
        return
    try:
        member = await get_user(member_id)
    except Exception:
        logger.exception("match_pick_family: get_user failed member_id=%s", member_id)
        member = None
    member_birth = ""
    if member is not None and "hd_birth_data" in member.keys():
        member_birth = (member["hd_birth_data"] or "").strip()
    if not member_birth:
        await callback.answer(msg.TXT_HD_MATCH_FAMILY_MEMBER_NO_DATA, show_alert=True)
        return
    await state.set_state(UserFlow.WAITING_PARTNER_DATA)
    # Симулируем «пользователь ввёл текст» — single-shot подача в существующий процесс.
    fake_message = callback.message
    fake_message_text = member_birth
    # Эмулируем поток через прямой вызов: обработчик match_process читает .text,
    # поэтому используем bot.send_message → handler.
    # В простом виде — просто кладём данные в state и просим юзера подтвердить.
    await state.update_data(match_partner_prefill=member_birth)
    await fake_message.answer(
        f"✅ Подтянул данные партнёра: <code>{member_birth}</code>\n"
        f"Отправь любое сообщение, чтобы запустить расчёт совместимости 🐎⚡️.",
        parse_mode=ParseMode.HTML,
    )
    _ = fake_message_text

