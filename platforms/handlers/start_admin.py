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
    hd_match_family_picker_keyboard,
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
    send_activation_success,
    send_start_paywall_screen,
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
    reset_admin_daily_advice_test_state,
    sales_stats_as_dict,
    set_user_accepted_terms,
    set_user_tariff,
    try_begin_daily_advice,
    update_balance,
)
from services.telegram_safe_text import sanitize_telegram_plain_text
from services.use_cases.animate_generation_turn import AnimateGenOutcome, run_animate_generation_turn
from platforms.telegram_chat_action import chat_action_loop
from platforms.telegram_chat_stream import create_throttled_stream_reply
from platforms.telegram_chunks import answer_chat_text
from platforms.telegram_notify import safe_send_user_message
from services.use_cases.chat_turn import ChatTurnOutcome, run_chat_turn
from services.use_cases.music_generation_turn import MusicGenOutcome, run_music_generation_turn
from services.use_cases.cabinet_turn import build_cabinet_view
from services.use_cases.payment_invoice_turn import InvoiceBuildOutcome, build_payment_invoice_draft
from services.use_cases.payment_shop_turn import build_tariffs_entry_text
from services.use_cases.payment_turn import PaymentApplyOutcome, run_successful_payment_apply
from services.use_cases.photo_generation_turn import PhotoGenOutcome, run_photo_generation_turn
from services.use_cases.promo_turn import PromoOutcome, run_promo_redeem
from services.referral_channel import grant_referral_channel_activation_bonus
from services.tos import accept_tos, is_tos_accepted
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


async def _notify_referral_channel_bonus(invited_user_id: int) -> None:
    """Начислить реф-бонус и попытаться уведомить пригласителя.

    Отправка пуша — best-effort: если пригласитель заблокировал бота
    или его чат недоступен, бонус остаётся в БД (он уже начислен в
    транзакции выше), а конвейер не падает. См. safe_send_user_message —
    там специализированные aiogram.exceptions с лог-уровнями (INFO для
    Forbidden, WARNING для RetryAfter / BadRequest, ERROR для unknown).
    """

    inviter_id = await grant_referral_channel_activation_bonus(
        invited_user_id,
        settings.referral_channel_crystals,
    )
    if not inviter_id:
        return

    delivered = await safe_send_user_message(
        deps.bot(),
        inviter_id,
        msg.TXT_REFERRAL_CHANNEL_BONUS,
        context="ref_channel_bonus",
        parse_mode=ParseMode.HTML,
    )
    if not delivered:
        # Бонус начислен; пуш не дошёл (юзер заблокировал бота и т.п.).
        # Это нормальная штатная ситуация, лог уже записан в helper'е.
        logger.info(
            "ref bonus push not delivered inviter_id=%s invited_id=%s "
            "(бонус в БД остаётся)",
            inviter_id,
            invited_user_id,
        )


async def _activate_after_paywall(
    target: Message,
    uid: int,
    *,
    username: str | None,
    pending_start: str | None,
    state: FSMContext | None,
) -> bool:
    """Принять условия, проверить доступ и показать главное меню. Возвращает True при успехе."""
    await set_user_accepted_terms(uid, accepted=True)
    result = await run_start_turn(
        settings,
        uid,
        username,
        pending_start,
        is_subscribed=is_subscribed,
    )
    if result.outcome is not StartFlowOutcome.WELCOME_MAIN_MENU:
        await send_start_paywall_screen(target, state)
        return False
    await send_activation_success(target, uid, state=state)
    await _notify_referral_channel_bonus(uid)
    if state is not None:
        await state.clear()
    return True


def _tos_gate_keyboard() -> InlineKeyboardMarkup:
    """Карточка TOS-gate: одна кнопка ``accept_legal_tos`` (без альтернатив)."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_TOS_ACCEPT_BTN,
                    callback_data=msg.CB_ACCEPT_LEGAL_TOS,
                )
            ]
        ]
    )


async def _send_tos_legal_gate(target: Message) -> None:
    """HTML-карточка с тремя гиперссылками Telegra.ph и кнопкой принятия."""

    text = msg.TXT_TOS_WELCOME_GATE.format(
        offer_url=settings.service_offer_url,
        privacy_url=settings.privacy_policy_url,
        subscription_url=settings.subscription_terms_url,
    )
    await target.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=_tos_gate_keyboard(),
        disable_web_page_preview=True,
    )


async def _continue_after_tos(
    target: Message,
    uid: int,
    *,
    username: str | None,
    pending_start: str | None,
    state: FSMContext,
) -> None:
    """Стандартный путь после принятия TOS: paywall канала ИЛИ главное меню."""

    result = await run_start_turn(
        settings,
        uid,
        username,
        pending_start,
        is_subscribed=is_subscribed,
    )
    if result.outcome is StartFlowOutcome.NEED_PAYWALL:
        await state.update_data(pending_start_text=pending_start or "")
        await send_start_paywall_screen(target, state)
        return
    await send_activation_success(target, uid, state=state)
    await _notify_referral_channel_bonus(uid)


@router.message(Command("start"))
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id
    uname = message.from_user.username if message.from_user else None

    # Шлагбаум TOS: если флаг is_tos_accepted == False — показываем ОДНУ
    # премиальную карточку с тремя Telegra.ph ссылками и единственной
    # кнопкой принятия. Никакого доступа к меню и «Базовой версии» до клика.
    if not await is_tos_accepted(uid):
        await state.update_data(pending_start_text=message.text or "")
        await _send_tos_legal_gate(message)
        return

    await _continue_after_tos(
        message,
        uid,
        username=uname,
        pending_start=message.text,
        state=state,
    )


@router.callback_query(F.data == msg.CB_ACCEPT_LEGAL_TOS)
async def cb_accept_legal_tos(callback: CallbackQuery, state: FSMContext) -> None:
    """Юзер согласился с офертой / политикой / подпиской — фиксируем флаг
    и плавно открываем интерфейс. Повторно этот экран больше не появится.

    Контракт безопасности: после ``accept_tos(uid)`` юзер **гарантированно**
    получает следующий экран (paywall канала или главное меню). Любой
    падающий шаг логируется CRITICAL и НЕ оставляет юзера на чёрном
    экране — fallback'ом отправляем главное меню. Это критично, потому
    что точка обрыва после accept_tos = юзер «застрял» (TOS-карточка
    больше не покажется, бот выглядит мёртвым)."""

    await callback.answer(msg.TXT_TOS_ACCEPTED_FLASH)
    if callback.message is None or callback.from_user is None:
        return
    uid = callback.from_user.id
    uname = callback.from_user.username

    try:
        await accept_tos(uid)
    except Exception:
        # accept_tos упал — БД недоступна или схема рассинхронизирована.
        # Юзер всё равно увидит fallback ниже; повторный /start
        # позволит перезапустить TOS-флоу когда БД восстановится.
        logger.critical(
            "cb_accept_legal_tos: accept_tos failed for uid=%s",
            uid, exc_info=True,
        )

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    fsm_data = await state.get_data()
    pending = fsm_data.get("pending_start_text")
    pending_text = pending if isinstance(pending, str) else None

    try:
        await _continue_after_tos(
            callback.message,
            uid,
            username=uname,
            pending_start=pending_text,
            state=state,
        )
    except Exception:
        # Любая ошибка в run_start_turn / send_start_paywall_screen /
        # send_activation_success / _notify_referral_channel_bonus.
        # Юзер не должен застрять — отправляем минимальный fallback
        # с главным меню (если он подписан, это сработает; если нет —
        # ChannelGateMiddleware перехватит следующий клик и покажет
        # paywall как должно).
        logger.critical(
            "cb_accept_legal_tos: continuation failed for uid=%s — fallback main_menu",
            uid, exc_info=True,
        )
        await _send_tos_fallback_main_menu(callback.message, uid)


async def _send_tos_fallback_main_menu(target: Message, user_id: int) -> None:
    """Запасной выход после accept_tos: «доступ открыт» + главное меню.

    Используется ТОЛЬКО при падении основной цепочки ``_continue_after_tos``.
    Цель — гарантия, что юзер не остаётся на экране без действий после
    того, как нажал «Принять условия»."""

    try:
        await target.answer(
            "🚀 <b>Доступ открыт.</b> Выбери, с чего начнём:",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(user_id),
        )
    except Exception:
        logger.exception(
            "cb_accept_legal_tos: fallback main_menu send failed for uid=%s",
            user_id,
        )


@router.callback_query(F.data == msg.CB_ACCEPT_RULES)
async def accept_rules(callback: CallbackQuery, state: FSMContext) -> None:
    await check_subscription(callback, state)


@router.callback_query(F.data == msg.CB_CHECK_SUBSCRIPTION)
async def check_subscription(callback: CallbackQuery, state: FSMContext) -> None:
    uid = callback.from_user.id
    channel_sub().invalidate(uid)
    if not await is_subscribed_cached(uid):
        await callback.answer(msg.TXT_CHANNEL_GATE_FAIL, show_alert=True)
        return
    fsm_data = await state.get_data()
    pending_start = fsm_data.get("pending_start_text")
    pending = pending_start if isinstance(pending_start, str) else None
    ok = await _activate_after_paywall(
        callback.message,
        uid,
        username=callback.from_user.username,
        pending_start=pending,
        state=state,
    )
    if ok:
        await callback.answer(msg.TXT_CHANNEL_GATE_OK)
    else:
        await callback.answer()

@router.message(Command("reset"))
async def cmd_reset_dialog(message: Message, state: FSMContext) -> None:
    await state.clear()
    await clear_user_dialog_and_memory(message.from_user.id)
    await message.answer(msg.TXT_RESET_OK)

@router.message(Command("help"))
@router.message(Command("faq"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        msg.format_support_text(settings),
        parse_mode=ParseMode.HTML,
        reply_markup=support_faq_keyboard(),
    )


@router.message(Command("reset_terms"))
async def cmd_reset_terms(message: Message, state: FSMContext) -> None:
    """Сброс принятия оферты (только admin) — для проверки онбординга."""
    if not _is_admin(message.from_user.id):
        return
    await set_user_accepted_terms(message.from_user.id, accepted=False)
    await state.clear()
    await message.answer("Условия сброшены. Отправьте /start — увидите экран оферты.")


@router.message(Command("reset_me"))
async def cmd_reset_me(message: Message, state: FSMContext) -> None:
    """Сброс «Совета дня» для админа: лимит, lock, тестовый профиль новичка."""
    uid = message.from_user.id
    if not _is_admin(uid):
        return
    await reset_admin_daily_advice_test_state(uid)
    await state.clear()
    await message.answer(
        "✅ Профиль «Совета дня» сброшен для теста:\n"
        "• last_free_date — очищен\n"
        "• advice_pending_at — снят\n"
        "• hd_type и advice_birth_data — очищены\n\n"
        "Нажми 🔮 Совет дня. Если остался платный hd_birth_data — "
        "бот подтянет дату из полного разбора.",
    )


async def _admin_set_simulated_tariff(message: Message, tariff: str) -> None:
    """Временно меняет тариф админа в SQLite для теста UX (God Mode биллинг не трогает)."""
    uid = message.from_user.id
    if not _is_admin(uid):
        return
    await set_user_tariff(uid, tariff)
    await message.answer(
        f"✅ Тестовый тариф: <b>{tariff}</b>\n\n"
        "UI Нейротекста и ограничения ролей — как у выбранного тарифа.\n"
        "Списание ⚡/💎 не происходит, если в .env включён <code>GOD_MODE_ENABLED=1</code> "
        "и ваш ID в <code>ADMIN_IDS</code>.\n\n"
        "Команды: /set_free · /set_mini · /set_smart · /set_ultra",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("set_free"))
async def cmd_set_free(message: Message) -> None:
    await _admin_set_simulated_tariff(message, "FREE")


@router.message(Command("set_mini"))
async def cmd_set_mini(message: Message) -> None:
    await _admin_set_simulated_tariff(message, "MINI")


@router.message(Command("set_smart"))
async def cmd_set_smart(message: Message) -> None:
    await _admin_set_simulated_tariff(message, "SMART")


@router.message(Command("set_ultra"))
async def cmd_set_ultra(message: Message) -> None:
    await _admin_set_simulated_tariff(message, "ULTRA")

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

    # Family Sharing: если user — owner ULTRA-семьи, и кто-то из members уже
    # прошёл HD Premium (есть hd_birth_data) — предлагаем «мгновенный композит».
    family_members = await _collect_family_members_with_hd(user_id)
    if own_birth_data and family_members:
        await target.answer(
            msg.TXT_HD_MATCH_FAMILY_PICKER,
            reply_markup=hd_match_family_picker_keyboard(family_members),
            parse_mode=ParseMode.HTML,
        )
        return

    await state.set_state(UserFlow.WAITING_PARTNER_DATA)
    if own_birth_data:
        await target.answer(msg.format_match_ask_second(settings))
    else:
        await target.answer(msg.format_match_ask_both(settings))


async def _collect_family_members_with_hd(owner_id: int) -> list[tuple[int, str]]:
    """Список member_id из ULTRA-семьи с непустым ``hd_birth_data``.

    Возвращает пары ``(member_id, label)`` — label либо ``hd_type``, либо «ID Telegram».
    Пустой список → у owner нет членов семьи или ни у кого нет HD-данных.
    """
    try:
        from services.family_sharing import list_family_members

        members = await list_family_members(owner_id)
    except Exception:
        return []
    out: list[tuple[int, str]] = []
    for mid in members:
        try:
            row = await get_user(mid)
        except Exception:
            continue
        birth = ""
        if "hd_birth_data" in row.keys():
            birth = (row["hd_birth_data"] or "").strip()
        if not birth:
            continue
        hd_type = ""
        if "hd_type" in row.keys():
            hd_type = (row["hd_type"] or "").strip()
        label = hd_type or f"ID {mid}"
        out.append((mid, label))
    return out

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

