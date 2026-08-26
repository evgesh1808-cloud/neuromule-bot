"""Telegram handlers."""
from __future__ import annotations

import asyncio
import html
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from aiogram import F, Router, types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
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
    channel_subscribe_markup,
    create_menu,
    get_admin_inline_keyboard,
    hd_menu,
    hd_report_sections_markup,
    hd_compatibility_result_markup,
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
    notify_admins_hd_report,
    send_same_as_instruction_button,
    try_dispatch_reply_nav_button,
)
from services import payments_catalog as paycat
from services.billing import billing
from services.billing.hd_pipeline import spend_hd_advice
from services.god_mode import billing_bypass
from services.billing.pricing import HD_ADVICE_COST
from services.billing.store import refund_charge
from services.hd_logic import (
    birth_data_minimum_for_advice,
    build_hd_math_data,
    change_user_crystals,
    compatibility_report_to_json,
    create_hd_premium_pdf,
    daily_advice_user_profile_from_repo_user,
    ensure_modern_hd_report,
    format_compatibility_telegram_html,
    format_premium_report,
    format_hd_congrats_html,
    generate_compatibility_report,
    generate_instagram_stories,
    generate_instagram_stories_async,
    generate_premium_bodygraph,
    generate_premium_report,
    generate_premium_report_resilient,
    get_dynamic_cta_for_today,
    get_user,
    md_to_telegram_html,
    parse_birth_for_daily_advice,
    parse_hd_request,
    premium_report_from_json,
    premium_report_to_json,
    is_legacy_hd_report_raw,
    today_iso,
    try_consume_crystals,
    update_user,
)
from services.daily_advice_pool import (
    advice_date_iso_msk,
    assemble_daily_advice_from_pool,
    builtin_pool_row,
    resolve_hd_pool_key,
    resolve_pool_row_for_request,
)
from services.hd_day_sky import resolve_energy_wave
from services.repository import (
    add_promo_code,
    clear_user_dialog_and_memory,
    commit_daily_advice,
    ensure_user,
    get_sales_stats,
    get_user_row,
    list_all_user_ids,
    rollback_daily_advice,
    reset_user_hd_state,
    sales_stats_as_dict,
    set_user_accepted_terms,
    try_begin_daily_advice,
    update_balance,
    update_user_last_advice_id,
)
from services.telegram_safe_text import sanitize_telegram_plain_text
from services.use_cases.animate_generation_turn import AnimateGenOutcome, run_animate_generation_turn
from platforms.telegram_chat_action import chat_action_loop
from platforms.telegram_chat_stream import create_throttled_stream_reply
from platforms.telegram_chunks import answer_chat_text, send_chat_html
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


def _hd_user_gender(user_row) -> str:
    from services.hd_logic import resolve_user_gender_from_row

    return resolve_user_gender_from_row(user_row)


def _hd_user_display_name(target: Message | CallbackQuery, user_row) -> str:
    if isinstance(target, CallbackQuery) and target.from_user is not None:
        name = (target.from_user.first_name or "").strip()
        if name:
            return name
    if isinstance(target, Message) and target.from_user is not None:
        name = (target.from_user.first_name or "").strip()
        if name:
            return name
    keys = user_row.keys() if hasattr(user_row, "keys") else ()
    if "username" in keys and user_row["username"]:
        return str(user_row["username"]).strip()
    return "друг"


async def _resolve_hd_report(
    uid: int,
    user_row,
    *,
    actor: Message | CallbackQuery,
    chat_id: int,
) -> tuple[dict | None, bool]:
    """Загружает отчёт; legacy v1 апгрейдит через Pro-движок без повторной оплаты."""
    user_name = _hd_user_display_name(actor, user_row)
    raw = user_row["hd_report_json"] if "hd_report_json" in user_row.keys() else None
    if not is_legacy_hd_report_raw(raw):
        return premium_report_from_json(raw), False
    async with chat_action_loop(deps.bot(), chat_id, "typing"):
        try:
            report, upgraded = await ensure_modern_hd_report(uid, user_name=user_name)
        except Exception:
            logger.exception("HD legacy upgrade failed uid=%s", uid)
            raise
    return report, upgraded


async def _deliver_upgraded_hd_report(
    target: Message,
    uid: int,
    user_row,
    report: dict,
    *,
    upgraded: bool,
) -> None:
    birth_data = (user_row["hd_birth_data"] or "").strip() if "hd_birth_data" in user_row.keys() else ""
    hd_type = (user_row["hd_type"] or "") if "hd_type" in user_row.keys() else ""
    math_data = build_hd_math_data(hd_type, birth_data)
    resolved_type = str(math_data.get("hd_type") or hd_type)
    await _answer_hd_html(
        target,
        format_hd_congrats_html(
            report,
            resolved_type,
            intro=msg.TXT_HD_UPGRADED_REPORT if upgraded else msg.TXT_HD_REPORT_READY,
        ),
        uid,
    )
    if upgraded:
        await _send_hd_premium_pdf(
            target,
            uid,
            report,
            birth_data,
            resolved_type,
            notify_admins=True,
            user_display_name=_hd_user_display_name(target, user_row),
        )
        try:
            story_relpaths = await generate_instagram_stories_async(
                uid,
                report,
                math_data=math_data,
                hd_type=resolved_type,
                birth_data=birth_data,
            )
        except Exception:
            logger.warning("instagram stories after upgrade failed uid=%s", uid, exc_info=True)
            story_relpaths = []
        await _send_hd_instagram_stories_album(
            target,
            uid,
            _hd_user_display_name(target, user_row),
            story_relpaths,
        )


@router.callback_query(F.data == msg.CB_HD_PREMIUM_BUY)
async def hd_premium_buy(callback: CallbackQuery, state: FSMContext) -> None:
    uid = callback.from_user.id
    user = await get_user(uid)
    has_pro = bool(user["has_pro_analysis"]) if "has_pro_analysis" in user.keys() else False
    if has_pro:
        raw = user["hd_report_json"] if "hd_report_json" in user.keys() else None
        if is_legacy_hd_report_raw(raw):
            birth_data = (user["hd_birth_data"] or "").strip() if "hd_birth_data" in user.keys() else ""
            if birth_data:
                await callback.answer(msg.TXT_HD_UPGRADING_REPORT_ALERT, show_alert=True)
                await callback.message.answer(msg.TXT_HD_UPGRADING_REPORT, parse_mode=ParseMode.HTML)
                try:
                    report, upgraded = await _resolve_hd_report(
                        uid,
                        user,
                        actor=callback,
                        chat_id=callback.message.chat.id,
                    )
                except Exception:
                    await callback.message.answer(
                        msg.TXT_HD_UPGRADE_FAILED,
                        parse_mode=ParseMode.HTML,
                    )
                    return
                if report and upgraded:
                    await _deliver_upgraded_hd_report(
                        callback.message,
                        uid,
                        user,
                        report,
                        upgraded=True,
                    )
                elif report:
                    await callback.message.answer(
                        msg.TXT_HD_ALREADY_PURCHASED,
                        reply_markup=hd_menu(True),
                        parse_mode=ParseMode.HTML,
                    )
                else:
                    await callback.message.answer(msg.TXT_HD_REPORT_NOT_FOUND, parse_mode=ParseMode.HTML)
                return
            await state.set_state(UserFlow.waiting_hd_birth_data)
            await state.update_data(hd_regenerate=True)
            await callback.message.answer(
                msg.TXT_HD_REGENERATE_NEED_BIRTH.format(cost=settings.cost_hd),
                parse_mode=ParseMode.HTML,
            )
            await callback.answer()
            return
        await callback.answer(msg.TXT_HD_UPGRADING_REPORT_ALERT, show_alert=True)
        await _start_hd_regenerate_for_user(callback.message, uid, state=state)
        return
    if not billing_bypass(uid) and not await is_subscribed(uid):
        await callback.message.answer(
            msg.TXT_HD_NEED_CHANNEL,
            reply_markup=channel_subscribe_markup(),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer(msg.TXT_HD_NEED_CHANNEL_ALERT, show_alert=True)
        return
    crystals = int(user["crystals"] or 0)
    if not billing_bypass(uid) and crystals < settings.cost_hd:
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


def _display_name_for_advice(target: Message, user_row) -> str:
    tg_name = ""
    if target.from_user is not None:
        tg_name = (target.from_user.first_name or "").strip()
    if tg_name:
        return tg_name
    keys = user_row.keys() if hasattr(user_row, "keys") else ()
    if "username" in keys and user_row["username"]:
        return str(user_row["username"]).strip()
    return "друг"


_MSK = timezone(timedelta(hours=3))


def _user_col(user_row, name: str, default=None):
    keys = user_row.keys() if hasattr(user_row, "keys") else ()
    if name not in keys:
        return default
    return user_row[name]


async def _compose_daily_advice_text(target: Message, user, user_profile: dict) -> str:
    """0 LLM: пул + эфемериды → готовый текст совета."""
    pool_key = resolve_hd_pool_key(user_profile.get("hd_type", ""))
    advice_day = advice_date_iso_msk()
    try:
        pool_row = await resolve_pool_row_for_request(pool_key)
    except Exception:
        logger.exception("daily advice pool resolve failed key=%s", pool_key)
        pool_row = builtin_pool_row(pool_key, advice_date=advice_day)

    birth_raw = (user_profile.get("birth_raw") or "").strip()
    if not birth_raw:
        birth_raw = " ".join(
            [
                user_profile.get("birth_date", ""),
                user_profile.get("birth_time", ""),
                user_profile.get("birth_place", ""),
            ]
        ).strip()
    energy_wave = resolve_energy_wave(birth_raw=birth_raw, advice_date=advice_day)
    assemble_kwargs = {
        "display_name": _display_name_for_advice(target, user),
        "birth_date": user_profile.get("birth_date", ""),
        "birth_time": user_profile.get("birth_time", ""),
        "birth_place": user_profile.get("birth_place", ""),
        "user_role": user_profile.get("user_role", ""),
        "cta_text": get_dynamic_cta_for_today(),
        "energy_wave": energy_wave,
    }
    text_out = sanitize_telegram_plain_text(
        assemble_daily_advice_from_pool(pool_row, **assemble_kwargs)
    )
    if text_out.strip():
        return text_out
    return sanitize_telegram_plain_text(
        assemble_daily_advice_from_pool(
            builtin_pool_row(pool_key, advice_date=advice_day),
            **assemble_kwargs,
        )
    )


async def _refresh_or_resend_daily_advice(
    target: Message,
    uid: int,
    user,
    *,
    callback: CallbackQuery | None = None,
) -> None:
    """Повторный клик в тот же день: Callback → edit; Reply-кнопка → короткое напоминание."""
    display_name = _display_name_for_advice(target, user)

    # Reply-кнопка меню: не дублируем простыню, только короткое предупреждение.
    if callback is None:
        await target.answer(
            f"🔮 {display_name}, твой прогноз на сегодня уже рассчитан выше! "
            "Новый космос откроется завтра после 00:05 МСК. "
            "Твой баланс энергии не потрачен. ✨"
        )
        return

    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

    user_profile = daily_advice_user_profile_from_repo_user(user)
    if user_profile is None:
        await target.answer(msg.TXT_HD_DAILY_ADVICE_ALREADY_TODAY)
        return

    try:
        original_pool_text = await _compose_daily_advice_text(target, user, user_profile)
    except Exception:
        logger.exception("daily advice refresh compose failed uid=%s", uid)
        await target.answer(msg.TXT_HD_DAILY_ADVICE_ALREADY_TODAY)
        return

    kb = _daily_advice_full_report_keyboard()
    current_time_str = datetime.now(_MSK).strftime("%H:%M:%S")
    refreshed_text = (
        f"✨ Твой Барометр обновлён на момент: {current_time_str} МСК\n\n"
        f"{original_pool_text}"
    )
    mid_raw = _user_col(user, "last_advice_message_id")
    try:
        mid = int(mid_raw) if mid_raw is not None else 0
    except (TypeError, ValueError):
        mid = 0

    if mid > 0:
        try:
            await target.bot.edit_message_text(
                chat_id=uid,
                message_id=mid,
                text=refreshed_text,
                reply_markup=kb,
            )
            return
        except Exception:
            logger.info(
                "daily advice edit failed uid=%s mid=%s — resending",
                uid,
                mid,
                exc_info=True,
            )

    try:
        new_msg = await target.answer(original_pool_text, reply_markup=kb)
    except TelegramBadRequest:
        new_msg = await target.answer(original_pool_text)
    await update_user_last_advice_id(uid, new_msg.message_id)


async def _send_daily_advice(
    target: Message,
    uid: int,
    state: FSMContext | None = None,
    *,
    callback: CallbackQuery | None = None,
) -> None:
    """Пул «Совета дня»: лимит → lock → birth → эфемериды + assemble (0 LLM)."""
    user = await get_user(uid)
    today = today_iso()

    if not billing_bypass(uid) and (user["last_free_date"] or "") == today:
        await _refresh_or_resend_daily_advice(target, uid, user, callback=callback)
        return

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

    if not await try_begin_daily_advice(uid):
        if charge_id:
            await refund_charge(charge_id)
        await target.answer(msg.TXT_HD_DAILY_ADVICE_BUSY)
        return

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

    if callback is not None:
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass

    pool_key = resolve_hd_pool_key(user_profile.get("hd_type", ""))
    advice_day = advice_date_iso_msk()

    try:
        final_text = await _compose_daily_advice_text(target, user, user_profile)
        kb = _daily_advice_full_report_keyboard()
        try:
            sent = await target.answer(final_text, reply_markup=kb)
        except TelegramBadRequest:
            sent = await target.answer(final_text)
        await update_user_last_advice_id(uid, sent.message_id)
        await commit_daily_advice(uid)
    except Exception:
        await rollback_daily_advice(uid)
        if charge_id:
            await refund_charge(charge_id)
        logger.exception("hd_free_advice_failed user_id=%s", uid)
        try:
            sent = await target.answer(
                sanitize_telegram_plain_text(
                    assemble_daily_advice_from_pool(
                        builtin_pool_row(pool_key, advice_date=advice_day),
                        display_name=_display_name_for_advice(target, user),
                        birth_date=user_profile.get("birth_date", ""),
                        birth_time=user_profile.get("birth_time", ""),
                        birth_place=user_profile.get("birth_place", ""),
                        user_role=user_profile.get("user_role", ""),
                        cta_text=get_dynamic_cta_for_today(),
                        energy_wave="мягкая волна ясности",
                    )
                )
            )
            await update_user_last_advice_id(uid, sent.message_id)
            await commit_daily_advice(uid)
        except Exception:
            logger.exception("hd_free_advice ultimate fallback failed uid=%s", uid)
            await target.answer(msg.TXT_HD_DAILY_ADVICE_GENERATION_FAILED)

@router.message(UserFlow.waiting_advice_birth, Command("cancel"))
async def advice_birth_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(msg.TXT_ADVICE_BIRTH_CANCELLED, parse_mode=ParseMode.HTML)

@router.message(UserFlow.waiting_advice_birth, F.text)
async def advice_birth_save(message: Message, state: FSMContext) -> None:
    if await try_dispatch_reply_nav_button(message, state):
        return
    raw = (message.text or "").strip()
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
    # Повторный клик в тот же день обрабатывает _send_daily_advice (edit/resend).
    await _send_daily_advice(callback.message, uid, state, callback=callback)

def _hd_section_html(title: str, body: str) -> str:
    return f"<b>{html.escape(title)}</b>\n\n{md_to_telegram_html(body)}"


async def _answer_hd_html(
    message: Message,
    text: str,
    uid: int,
) -> None:
    """Текст HD + клавиатура разделов; без WebApp при ошибке Telegram."""
    markups: list[InlineKeyboardMarkup | None] = [
        hd_report_sections_markup(uid, include_webapp=True),
        hd_report_sections_markup(uid, include_webapp=False),
        None,
    ]
    for markup in markups:
        try:
            await answer_chat_text(message, text, settings, reply_markup=markup)
            return
        except TelegramBadRequest:
            logger.warning(
                "hd answer markup rejected uid=%s include_webapp=%s",
                uid,
                markup is markups[0],
                exc_info=True,
            )
    await answer_chat_text(message, text, settings, reply_markup=None)


async def _send_hd_section_message(
    message: Message,
    uid: int,
    body_text: str,
) -> None:
    """Раздел отчёта — только новые сообщения (chunked), без edit_text."""
    await _answer_hd_html(message, body_text, uid)


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


async def _send_hd_premium_pdf(
    message: Message,
    uid: int,
    report: dict,
    birth_data: str,
    hd_type: str,
    *,
    notify_admins: bool = False,
    user_display_name: str = "",
    deliver_to_chat_id: int | None = None,
) -> None:
    pdf_path: str | None = None
    chat_id = deliver_to_chat_id if deliver_to_chat_id is not None else message.chat.id
    bot = deps.bot()
    pdf_user_name = user_display_name or (
        (message.from_user.first_name or "").strip() if message.from_user else ""
    )
    try:
        async with chat_action_loop(bot, chat_id, "upload_document"):
            pdf_path = create_hd_premium_pdf(
                uid,
                report,
                birth_data,
                hd_type=hd_type,
                user_name=pdf_user_name,
            )
            await bot.send_document(
                chat_id=chat_id,
                document=FSInputFile(pdf_path),
                caption=msg.TXT_HD_PDF_CAPTION,
                parse_mode=ParseMode.HTML,
            )
            if notify_admins:
                bg_path = _PROJECT_ROOT / "tmp" / f"ready_hd_{uid}.png"
                await notify_admins_hd_report(
                    bot,
                    payer_uid=uid,
                    user_name=user_display_name or pdf_user_name or "клиент",
                    hd_type=hd_type,
                    birth_data=birth_data,
                    pdf_path=pdf_path,
                    bodygraph_path=bg_path if bg_path.is_file() else None,
                )
    except TelegramForbiddenError:
        logger.info("hd_premium_pdf forbidden uid=%s", uid)
    except TelegramBadRequest as exc:
        logger.warning("hd_premium_pdf bad_request uid=%s: %s", uid, exc)
    except Exception:
        logger.exception("hd_premium_pdf_failed uid=%s", uid)
    finally:
        if pdf_path:
            try:
                Path(pdf_path).unlink(missing_ok=True)
            except OSError:
                logger.warning("failed_remove_hd_pdf path=%s", pdf_path)


async def _send_hd_instagram_stories_album(
    message: Message,
    uid: int,
    user_name: str,
    story_relpaths: list[str],
    *,
    deliver_to_chat_id: int | None = None,
) -> None:
    files = [_PROJECT_ROOT / rel for rel in story_relpaths if (_PROJECT_ROOT / rel).is_file()]
    if len(files) < 2:
        logger.warning("instagram stories album skipped uid=%s files=%s", uid, len(files))
        return

    chat_id = deliver_to_chat_id if deliver_to_chat_id is not None else message.chat.id
    bot = deps.bot()
    display_name = html.escape((user_name or "друг").strip() or "друг")
    caption = msg.TXT_HD_INSTAGRAM_ALBUM_CAPTION.format(name=display_name)
    media = [
        InputMediaPhoto(
            media=FSInputFile(str(files[0])),
            caption=caption,
            parse_mode=ParseMode.HTML,
        ),
        InputMediaPhoto(media=FSInputFile(str(files[1]))),
    ]
    try:
        async with chat_action_loop(bot, chat_id, "upload_photo"):
            await bot.send_media_group(chat_id=chat_id, media=media)
    except TelegramForbiddenError:
        logger.info("instagram album forbidden uid=%s", uid)
    except TelegramBadRequest as exc:
        logger.warning("instagram album bad_request uid=%s: %s", uid, exc)
    except Exception:
        logger.exception("instagram album failed uid=%s", uid)


async def _send_hd_congrats_to_chat(bot, chat_id: int, text: str, uid: int) -> None:
    for include_webapp in (True, False):
        try:
            await send_chat_html(
                bot,
                chat_id,
                text,
                settings,
                reply_markup=hd_report_sections_markup(uid, include_webapp=include_webapp),
            )
            return
        except TelegramBadRequest:
            logger.warning(
                "hd congrats markup rejected uid=%s include_webapp=%s",
                uid,
                include_webapp,
                exc_info=True,
            )
    await send_chat_html(bot, chat_id, text, settings, reply_markup=None)


async def _deliver_hd_premium_bundle(
    message: Message,
    uid: int,
    user_row: Any,
    report: dict,
    *,
    birth_data: str,
    hd_type: str,
    intro: str,
    deliver_to_chat_id: int | None = None,
    user_display_name: str | None = None,
) -> None:
    chat_id = deliver_to_chat_id if deliver_to_chat_id is not None else message.chat.id
    bot = deps.bot()
    resolved_display_name = user_display_name or _hd_user_display_name(message, user_row)
    math_data = build_hd_math_data(hd_type, birth_data)
    resolved_type = str(math_data.get("hd_type") or hd_type)
    defined = math_data.get("defined_centers") or []
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda d=defined, u=uid: generate_premium_bodygraph(list(d), u),
        )
    except Exception:
        logger.warning("bodygraph generation failed uid=%s", uid, exc_info=True)
    story_relpaths: list[str] = []
    try:
        story_relpaths = await generate_instagram_stories_async(
            uid,
            report,
            math_data=math_data,
            hd_type=resolved_type,
            birth_data=birth_data,
        )
    except Exception:
        logger.warning("instagram stories generation failed uid=%s", uid, exc_info=True)
    await _send_hd_congrats_to_chat(
        bot,
        chat_id,
        format_hd_congrats_html(report, resolved_type, intro=intro),
        uid,
    )
    await _send_hd_premium_pdf(
        message,
        uid,
        report,
        birth_data,
        resolved_type,
        notify_admins=True,
        user_display_name=resolved_display_name,
        deliver_to_chat_id=chat_id,
    )
    await _send_hd_instagram_stories_album(
        message,
        uid,
        resolved_display_name,
        story_relpaths,
        deliver_to_chat_id=chat_id,
    )


async def _run_free_hd_regenerate(
    message: Message,
    uid: int,
    user_row: Any,
    *,
    hd_type: str,
    birth_data: str,
    deliver_to_chat_id: int | None = None,
    user_display_name: str | None = None,
) -> None:
    chat_id = deliver_to_chat_id if deliver_to_chat_id is not None else message.chat.id
    user_name = user_display_name or _hd_user_display_name(message, user_row)
    user_gender = _hd_user_gender(user_row)
    keys = user_row.keys() if hasattr(user_row, "keys") else ()
    existing_raw = user_row["hd_report_json"] if "hd_report_json" in keys else None
    async with chat_action_loop(deps.bot(), chat_id, "typing"):
        report, llm_ok = await generate_premium_report_resilient(
            hd_type,
            birth_data,
            user_name=user_name,
            user_gender=user_gender,
            existing_raw=existing_raw,
        )
    if not report:
        raise RuntimeError("Gemini returned empty HD report")
    math_data = build_hd_math_data(hd_type, birth_data)
    resolved_type = str(math_data.get("hd_type") or hd_type)
    await update_user(
        uid,
        hd_report_json=premium_report_to_json(report),
        hd_type=resolved_type,
        hd_birth_data=birth_data,
        has_pro_analysis=1,
    )
    intro = msg.TXT_HD_UPGRADED_REPORT if llm_ok else msg.TXT_HD_UPGRADED_OFFLINE
    await _deliver_hd_premium_bundle(
        message,
        uid,
        user_row,
        report,
        birth_data=birth_data,
        hd_type=resolved_type,
        intro=intro,
        deliver_to_chat_id=deliver_to_chat_id,
        user_display_name=user_name,
    )


def _parse_admin_hd_command(text: str, *, default_uid: int) -> tuple[int, str]:
    parts = (text or "").strip().split()
    if len(parts) <= 1:
        return default_uid, ""
    if parts[1].isdigit():
        target_uid = int(parts[1])
        birth = " ".join(parts[2:]).strip() if len(parts) > 2 else ""
        return target_uid, birth
    return default_uid, " ".join(parts[1:]).strip()


async def _start_hd_regenerate_for_user(
    message: Message,
    uid: int,
    *,
    birth_data_override: str | None = None,
    deliver_to_chat_id: int | None = None,
    state: FSMContext | None = None,
    show_upgrading_notice: bool = True,
) -> bool:
    """Запуск бесплатной перегенерации HD. False — нужна дата рождения от пользователя."""
    user = await get_user(uid)
    has_pro = bool(user["has_pro_analysis"]) if "has_pro_analysis" in user.keys() else False
    if not has_pro and not (birth_data_override or "").strip():
        await message.answer(msg.TXT_HD_REPORT_NOT_FOUND, parse_mode=ParseMode.HTML)
        return False

    birth_data = (birth_data_override or "").strip()
    if not birth_data:
        birth_data = (user["hd_birth_data"] or "").strip() if "hd_birth_data" in user.keys() else ""
    hd_type = (user["hd_type"] or "не указан") if "hd_type" in user.keys() else "не указан"

    if not birth_data:
        if state is not None:
            await state.set_state(UserFlow.waiting_hd_birth_data)
            await state.update_data(hd_regenerate=True)
        await message.answer(msg.TXT_HD_REGENERATE_NEED_BIRTH, parse_mode=ParseMode.HTML)
        return False

    if (birth_data_override or "").strip():
        await update_user(uid, hd_birth_data=birth_data, has_pro_analysis=1)
        user = await get_user(uid)

    if show_upgrading_notice:
        await message.answer(msg.TXT_HD_UPGRADING_REPORT, parse_mode=ParseMode.HTML)

    delivery_chat = deliver_to_chat_id if deliver_to_chat_id is not None else uid

    async def _regenerate_job() -> None:
        try:
            await _run_free_hd_regenerate(
                message,
                uid,
                user,
                hd_type=hd_type,
                birth_data=birth_data,
                deliver_to_chat_id=delivery_chat,
            )
        except Exception:
            logger.exception("hd_regenerate_failed uid=%s", uid)
            try:
                await message.answer(msg.TXT_HD_UPGRADE_FAILED, parse_mode=ParseMode.HTML)
            except Exception:
                logger.exception("hd_regenerate_failed_notify uid=%s", uid)

    asyncio.create_task(_regenerate_job(), name=f"hd_regenerate_{uid}")
    return True


@router.message(Command("reset_hd"))
async def cmd_reset_hd(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    target_uid, _ = _parse_admin_hd_command(message.text or "", default_uid=message.from_user.id)
    await reset_user_hd_state(target_uid)
    await state.clear()
    await message.answer(
        msg.TXT_HD_ADMIN_RESET_OK.format(uid=target_uid),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("hd_refresh"))
async def cmd_hd_refresh(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    admin_uid = message.from_user.id
    target_uid, birth_override = _parse_admin_hd_command(message.text or "", default_uid=admin_uid)
    user = await get_user(target_uid)
    has_pro = bool(user["has_pro_analysis"]) if "has_pro_analysis" in user.keys() else False
    birth_stored = (user["hd_birth_data"] or "").strip() if "hd_birth_data" in user.keys() else ""
    if not birth_override and not birth_stored:
        await message.answer(
            msg.TXT_HD_ADMIN_REFRESH_NO_BIRTH.format(uid=target_uid),
            parse_mode=ParseMode.HTML,
        )
        return
    if not has_pro and not birth_override:
        await message.answer(
            msg.TXT_HD_ADMIN_REFRESH_NO_BIRTH.format(uid=target_uid),
            parse_mode=ParseMode.HTML,
        )
        return

    await message.answer(
        msg.TXT_HD_ADMIN_REFRESH_STARTED.format(uid=target_uid),
        parse_mode=ParseMode.HTML,
    )
    deliver_to = target_uid if target_uid != admin_uid else None
    started = await _start_hd_regenerate_for_user(
        message,
        target_uid,
        birth_data_override=birth_override or None,
        deliver_to_chat_id=deliver_to,
        show_upgrading_notice=False,
    )
    if started and target_uid != admin_uid:
        await message.answer(
            f"⏳ Перегенерация для <code>{target_uid}</code> запущена в фоне — "
            "3–8 мин, клиенту придут PDF и Stories.",
            parse_mode=ParseMode.HTML,
        )


@router.message(Command("hd_menu"))
async def cmd_hd_menu(message: Message) -> None:
    """Свежее inline-меню HD (кнопка «Обновить отчёт») — для себя или клиента."""
    if not _is_admin(message.from_user.id):
        return
    admin_uid = message.from_user.id
    target_uid, _ = _parse_admin_hd_command(message.text or "", default_uid=admin_uid)
    user = await get_user(target_uid)
    has_pro = bool(user["has_pro_analysis"]) if "has_pro_analysis" in user.keys() else False
    bot = deps.bot()
    await bot.send_message(
        chat_id=target_uid,
        text=msg.TXT_HD_SECTION_INTRO,
        reply_markup=hd_menu(has_pro),
        parse_mode=ParseMode.HTML,
    )
    if target_uid == admin_uid:
        note = "✅ Меню Human Design отправлено вам."
    else:
        note = f"✅ Меню Human Design отправлено пользователю <code>{target_uid}</code>."
    await message.answer(note, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == msg.CB_HD_REGENERATE)
async def hd_regenerate_callback(callback: CallbackQuery, state: FSMContext) -> None:
    uid = callback.from_user.id
    user = await get_user(uid)
    has_pro = bool(user["has_pro_analysis"]) if "has_pro_analysis" in user.keys() else False
    if not has_pro:
        await callback.answer(msg.TXT_HD_REPORT_NOT_FOUND_ALERT, show_alert=True)
        return
    birth_data = (user["hd_birth_data"] or "").strip() if "hd_birth_data" in user.keys() else ""
    if not birth_data:
        await state.set_state(UserFlow.waiting_hd_birth_data)
        await state.update_data(hd_regenerate=True)
        await callback.message.answer(msg.TXT_HD_REGENERATE_NEED_BIRTH, parse_mode=ParseMode.HTML)
        await callback.answer()
        return
    await callback.answer(msg.TXT_HD_UPGRADING_REPORT_ALERT, show_alert=True)
    await _start_hd_regenerate_for_user(callback.message, uid, state=state)


@router.message(UserFlow.waiting_hd_birth_data, F.text)
async def hd_premium_process(message: Message, state: FSMContext) -> None:
    if await try_dispatch_reply_nav_button(message, state):
        return
    uid = message.from_user.id
    raw = (message.text or "").strip()
    data = await state.get_data()
    regenerate = bool(data.get("hd_regenerate"))
    if not raw:
        await message.answer(msg.TXT_HD_EMPTY_DATA, parse_mode=ParseMode.HTML)
        return
    if not billing_bypass(uid) and not await is_subscribed(uid):
        await message.answer(
            msg.TXT_HD_NEED_CHANNEL,
            reply_markup=channel_subscribe_markup(),
            parse_mode=ParseMode.HTML,
        )
        return
    if regenerate:
        from services.billing.types import SpendResult

        user = await get_user(uid)
        has_pro = bool(user["has_pro_analysis"]) if "has_pro_analysis" in user.keys() else False
        if has_pro:
            spend = SpendResult(ok=True, charge=None)
        else:
            regenerate = False
    if not regenerate:
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

    try:
        async with chat_action_loop(deps.bot(), message.chat.id, "typing"):
            hd_type, birth_data = parse_hd_request(raw)
            user_row = await get_user(uid)
            user_name = _hd_user_display_name(message, user_row)
            user_gender = _hd_user_gender(user_row)
            report = await generate_premium_report(
                hd_type,
                birth_data,
                user_name=user_name,
                user_gender=user_gender,
            )
        if not report:
            raise RuntimeError("Gemini returned empty HD report")
        math_data = build_hd_math_data(hd_type, birth_data)
        resolved_type = str(math_data.get("hd_type") or hd_type)
        await update_user(
            uid,
            hd_report_json=premium_report_to_json(report),
            hd_type=resolved_type,
            hd_birth_data=birth_data,
            has_pro_analysis=1,
        )
        await _deliver_hd_premium_bundle(
            message,
            uid,
            user_row,
            report,
            birth_data=birth_data,
            hd_type=resolved_type,
            intro=msg.TXT_HD_UPGRADED_REPORT if regenerate else msg.TXT_HD_REPORT_READY,
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

@router.message(UserFlow.waiting_compatibility_data, F.text)
async def compatibility_process(message: Message, state: FSMContext) -> None:
    if await try_dispatch_reply_nav_button(message, state):
        return
    uid = message.from_user.id
    raw = (message.text or "").strip()
    data = await state.get_data()
    prefilled_partner = (data.get("match_partner_prefill") or "").strip()
    await state.clear()
    partner_raw = prefilled_partner or raw
    if not partner_raw:
        await message.answer(msg.TXT_MATCH_EMPTY_DATA)
        return

    await message.answer(msg.TXT_MATCH_PROCESSING)
    spend = await billing.spend_hd_compatibility(uid)
    if not spend.ok:
        if spend.error == "free_premium_create_blocked":
            from platforms.telegram_utils import send_free_create_blocked

            await send_free_create_blocked(message)
        else:
            await message.answer(
                msg.format_match_insufficient_crystals(settings),
                reply_markup=paycat.shop_packages_keyboard(),
            )
        return
    charge_id = spend.charge.charge_id if spend.charge else ""

    user_name = (message.from_user.first_name or "ты").strip() if message.from_user else "ты"
    try:
        async with chat_action_loop(deps.bot(), message.chat.id, "typing"):
            report = await generate_compatibility_report(
                uid,
                partner_raw,
                user_name=user_name,
                partner_name="партнёр",
            )
        await update_user(
            uid,
            match_partner_data=parse_hd_request(partner_raw)[1],
            hd_compatibility_json=compatibility_report_to_json(report),
        )
        await message.answer(
            format_compatibility_telegram_html(report, user_name=user_name, partner_name="партнёр"),
            reply_markup=hd_compatibility_result_markup(uid),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.exception("compatibility_failed user_id=%s", uid)
        if charge_id:
            await refund_charge(charge_id)
        await message.answer(msg.TXT_MATCH_FAILED, reply_markup=paycat.shop_packages_keyboard())


@router.message(UserFlow.waiting_compatibility_data)
async def compatibility_need_text(message: Message) -> None:
    await message.answer(msg.TXT_MATCH_EMPTY_DATA)


@router.message(UserFlow.WAITING_PARTNER_DATA, F.text)
async def match_process(message: Message, state: FSMContext) -> None:
    """Legacy alias → новый одноразовый flow совместимости."""
    await compatibility_process(message, state)

@router.message(UserFlow.WAITING_PARTNER_DATA)
async def match_need_text(message: Message) -> None:
    await compatibility_need_text(message)

@router.callback_query(F.data.startswith(msg.CB_HD_REPORT_PREFIX))
async def hd_report_section(callback: CallbackQuery) -> None:
    uid = callback.from_user.id
    if callback.message is None:
        await callback.answer()
        return
    try:
        await _hd_report_section_impl(callback, uid)
    except Exception:
        logger.exception("hd_report_section failed uid=%s data=%s", uid, callback.data)
        await callback.answer(msg.TXT_HD_UPGRADE_FAILED, show_alert=True)
        if callback.message is not None:
            await callback.message.answer(msg.TXT_HD_UPGRADE_FAILED, parse_mode=ParseMode.HTML)


async def _hd_report_section_impl(callback: CallbackQuery, uid: int) -> None:
    user = await get_user(uid)
    raw = user["hd_report_json"] if "hd_report_json" in user.keys() else None
    legacy = is_legacy_hd_report_raw(raw)
    if legacy:
        await callback.answer(msg.TXT_HD_UPGRADING_REPORT_ALERT, show_alert=True)
        await callback.message.answer(msg.TXT_HD_UPGRADING_REPORT, parse_mode=ParseMode.HTML)
    try:
        report, upgraded = await _resolve_hd_report(
            uid,
            user,
            actor=callback,
            chat_id=callback.message.chat.id,
        )
    except Exception:
        logger.exception("hd_report_section upgrade failed uid=%s", uid)
        await callback.answer()
        await callback.message.answer(msg.TXT_HD_UPGRADE_FAILED, parse_mode=ParseMode.HTML)
        return
    if report is None:
        await callback.answer(msg.TXT_HD_REPORT_NOT_FOUND_ALERT, show_alert=True)
        return
    if upgraded:
        await _deliver_upgraded_hd_report(callback.message, uid, user, report, upgraded=True)
        await callback.answer()
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
            hd_type_val = (user["hd_type"] or "") if "hd_type" in user.keys() else ""
            async with chat_action_loop(deps.bot(), callback.message.chat.id, "upload_document"):
                pdf_path = create_hd_premium_pdf(
                    uid,
                    report,
                    birth_data,
                    hd_type=hd_type_val,
                    user_name=_hd_user_display_name(callback, user),
                )
                await callback.message.answer_document(
                    FSInputFile(pdf_path),
                    caption=msg.TXT_HD_PDF_CAPTION,
                    parse_mode=ParseMode.HTML,
                )
        except Exception:
            logger.exception("hd pdf on demand failed uid=%s", uid)
            await callback.message.answer(msg.TXT_HD_UPGRADE_FAILED, parse_mode=ParseMode.HTML)
        finally:
            if pdf_path:
                try:
                    Path(pdf_path).unlink(missing_ok=True)
                except OSError:
                    logger.warning("failed_remove_hd_pdf path=%s", pdf_path)
        await callback.answer()
        return

    if section == "instagram":
        birth_data = (user["hd_birth_data"] or "").strip() if "hd_birth_data" in user.keys() else ""
        hd_type_val = (user["hd_type"] or "") if "hd_type" in user.keys() else ""
        math_data = build_hd_math_data(hd_type_val, birth_data)
        story_relpaths: list[str] = []
        try:
            story_relpaths = await generate_instagram_stories_async(
                uid,
                report,
                math_data=math_data,
                hd_type=hd_type_val,
                birth_data=birth_data,
            )
        except Exception:
            logger.warning("instagram stories on demand failed uid=%s", uid, exc_info=True)
        await _send_hd_instagram_stories_album(
            callback.message,
            uid,
            _hd_user_display_name(callback, user),
            story_relpaths,
        )
        await callback.answer()
        return

    if section not in titles:
        await callback.answer(msg.TXT_STUB_BUTTON, show_alert=True)
        return
    title = titles[section]
    section_body = str(
        report.get(section) or "Раздел пока пуст — нажми 🔄 Обновить отчёт ещё раз."
    ).strip()
    body_text = _hd_section_html(title, section_body)
    await _send_hd_section_message(callback.message, uid, body_text)
    await callback.answer()

@router.callback_query(F.data == msg.CB_HD_COMPATIBILITY_START)
async def hd_compatibility_start(callback: CallbackQuery, state: FSMContext) -> None:
    uid = callback.from_user.id
    user = await get_user(uid)
    has_pro = bool(user["has_pro_analysis"]) if "has_pro_analysis" in user.keys() else False
    if not has_pro:
        await callback.answer(msg.TXT_HD_COMPAT_LOCKED, show_alert=True)
        return
    if not billing_bypass(uid):
        if not await is_subscribed(uid):
            await callback.message.answer(
                msg.TXT_HD_NEED_CHANNEL,
                reply_markup=channel_subscribe_markup(),
                parse_mode=ParseMode.HTML,
            )
            await callback.answer(msg.TXT_HD_NEED_CHANNEL_ALERT, show_alert=True)
            return
        tariff = str(user["tariff"] or "Free") if "tariff" in user.keys() else "Free"
        if tariff.strip().lower() == "free":
            from platforms.telegram_utils import send_free_create_blocked

            await send_free_create_blocked(callback.message)
            await callback.answer()
            return
        crystals = int(user["crystals"] or 0)
        if crystals < settings.cost_match:
            await callback.message.answer(
                msg.format_match_insufficient_crystals(settings),
                reply_markup=paycat.shop_packages_keyboard(),
            )
            await callback.answer(
                msg.format_match_insufficient_crystals(settings),
                show_alert=True,
            )
            return
    own_birth = (user["hd_birth_data"] or "").strip() if "hd_birth_data" in user.keys() else ""
    await state.update_data(match_own_birth_data=own_birth or None)
    await state.set_state(UserFlow.waiting_compatibility_data)
    await callback.message.answer(msg.format_match_ask_second(settings))
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

