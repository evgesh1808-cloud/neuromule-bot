"""Telegram-интерфейс (aiogram). Тексты — в content/messages.py; логика — в services/."""
from __future__ import annotations

import logging
import random

from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message
from aiogram.types import PreCheckoutQuery
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from config import settings
from content import messages as msg
from services import payments_catalog as paycat
from services.dialog_write_worker import start_dialog_write_worker
from services.repository import (
    add_promo_code,
    clear_user_dialog_and_memory,
    get_sales_stats,
    init_db,
    list_all_user_ids,
    update_balance,
)
from services.use_cases.animate_generation_turn import AnimateGenOutcome, run_animate_generation_turn
from platforms.telegram_chat_stream import create_throttled_stream_reply
from platforms.telegram_chunks import answer_chat_text
from services.app_logging import setup_logging
from services.use_cases.chat_turn import ChatTurnOutcome, run_chat_turn
from services.use_cases.image_prompt_turn import ImagePromptOutcome, run_image_prompt_turn
from services.use_cases.music_generation_turn import MusicGenOutcome, run_music_generation_turn
from services.use_cases.cabinet_turn import build_cabinet_view
from services.use_cases.payment_invoice_turn import InvoiceBuildOutcome, build_payment_invoice_draft
from services.use_cases.payment_shop_turn import build_tariffs_entry_text
from services.use_cases.start_ui_turn import format_start_message_html, start_messages_link_preview_off
from services.use_cases.tariff_shop_nav_turn import TariffShopNavOutcome, resolve_tariff_shop_callback
from services.use_cases.payment_turn import PaymentApplyOutcome, run_successful_payment_apply
from services.use_cases.start_turn import StartFlowOutcome, run_start_turn
from services.use_cases.photo_generation_turn import PhotoGenOutcome, run_photo_generation_turn
from services.use_cases.promo_turn import PromoOutcome, run_promo_redeem
from services.use_cases.video_generation_turn import VideoGenOutcome, run_video_generation_turn


class UserFlow(StatesGroup):
    waiting_for_photo = State()
    waiting_for_video = State()
    waiting_for_music = State()
    waiting_for_animate = State()
    waiting_for_image_prompt = State()
    waiting_promo_code = State()
    waiting_admin_broadcast = State()


logger = logging.getLogger(__name__)


def _invite_switch_query() -> str:
    q = msg.INVITE_SWITCH_QUERY_TEMPLATE.format(
        bot_username=settings.telegram_bot_username.lstrip("@"),
    )
    return q[:256]


def main_menu() -> types.ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for text in msg.MAIN_MENU_BUTTONS:
        builder.button(text=text)
    builder.adjust(1, 2, 2)
    return builder.as_markup(resize_keyboard=True)


def create_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=t, callback_data=cb)] for t, cb in msg.CREATE_MENU_BUTTONS
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def image_model_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"{msg.CB_IMG_PREFIX}{mid}")]
        for label, mid in msg.IMAGE_MODELS
    ]
    rows.append(
        [InlineKeyboardButton(text=msg.TXT_BACK_TO_TOOLS, callback_data=msg.CB_BACK_CREATE)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cabinet_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_CABINET_INVITE_BUTTON,
                    switch_inline_query=_invite_switch_query(),
                )
            ],
            [InlineKeyboardButton(text=msg.TXT_CABINET_PROMO_BUTTON, callback_data=msg.CB_CABINET_PROMO)],
            [
                InlineKeyboardButton(
                    text=msg.TXT_CABINET_CHANNEL_PROMOS,
                    url=settings.channel_url,
                )
            ],
        ]
    )


def invite_limit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_CABINET_INVITE_BUTTON,
                    switch_inline_query=_invite_switch_query(),
                )
            ],
        ]
    )


def support_menu() -> InlineKeyboardMarkup:
    admin = settings.admin_username.lstrip("@").strip()
    support = settings.support_bot_username.lstrip("@")
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="💬 Написать в поддержку", url=f"https://t.me/{support}")],
    ]
    if admin:
        rows.append([InlineKeyboardButton(text="👤 Администратор", url=f"https://t.me/{admin}")])
    rows.extend(
        [
            [InlineKeyboardButton(text="📜 Правила сервиса", callback_data=msg.CB_SERVICE_RULES)],
            [InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data=msg.CB_BACK_MAIN)],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def channel_subscribe_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_SUBSCRIPTION_CHANNEL_BUTTON,
                    url=settings.channel_url,
                )
            ],
        ]
    )


def service_rules_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📜 Публичная оферта", url=settings.service_offer_url)],
            [InlineKeyboardButton(text="🔒 Политика конфиденциальности", url=settings.privacy_policy_url)],
            [InlineKeyboardButton(text="🔁 Условия подписки", url=settings.subscription_terms_url)],
            [InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data=msg.CB_BACK_MAIN)],
        ]
    )


def build_dispatcher() -> tuple[Bot, Dispatcher]:
    def _is_admin(user_id: int) -> bool:
        return user_id in set(settings.admin_ids)

    def admin_menu() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📊 Статистика", callback_data=msg.CB_ADMIN_STATS)],
                [InlineKeyboardButton(text="📢 Рассылка", callback_data=msg.CB_ADMIN_BROADCAST)],
            ]
        )

    bot = Bot(token=settings.tg_token)
    dp = Dispatcher()

    async def is_subscribed(user_id: int) -> bool:
        try:
            member = await bot.get_chat_member(chat_id=settings.channel_id, user_id=user_id)
            return member.status not in ("left", "kicked")
        except Exception:
            return True

    @dp.message(Command("start"))
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        uname = message.from_user.username if message.from_user else None
        st = await run_start_turn(
            settings,
            message.from_user.id,
            uname,
            message.text,
            is_subscribed=is_subscribed,
        )
        kw = st.template_kwargs
        no_preview = start_messages_link_preview_off()
        if st.outcome is StartFlowOutcome.NEED_CHANNEL:
            await message.answer(
                msg.TXT_START_FIRST_MEET_NEED_CHANNEL_1,
                parse_mode=ParseMode.HTML,
                link_preview_options=no_preview,
            )
            await message.answer(
                format_start_message_html(msg.TXT_START_FIRST_MEET_NEED_CHANNEL_2, kw),
                parse_mode=ParseMode.HTML,
                reply_markup=channel_subscribe_markup(),
                link_preview_options=no_preview,
            )
            return
        await message.answer(
            format_start_message_html(msg.TXT_START_FIRST_MEET_OK, kw),
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
            link_preview_options=no_preview,
        )

    @dp.message(Command("reset"))
    async def cmd_reset_dialog(message: Message, state: FSMContext) -> None:
        await state.clear()
        await clear_user_dialog_and_memory(message.from_user.id)
        await message.answer(msg.TXT_RESET_OK)

    @dp.message(Command("admin"))
    async def admin_panel(message: Message, state: FSMContext) -> None:
        uid = message.from_user.id
        if not _is_admin(uid):
            await message.answer(msg.TXT_ADMIN_DENIED)
            return
        await state.clear()
        logger.info("admin_panel_open user_id=%s", uid)
        await message.answer(msg.TXT_ADMIN_PANEL, reply_markup=admin_menu())

    @dp.message(Command("give_energy"))
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
        if amount == 0:
            await message.answer("amount не может быть 0.")
            return
        await update_balance(target_id, "energy", amount)
        logger.info("admin_give_energy admin_id=%s target_id=%s amount=%s", uid, target_id, amount)
        await message.answer(f"Готово: user_id={target_id}, начислено {amount} ⚡")

    @dp.message(Command("add_promo"))
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

    @dp.callback_query(F.data == msg.CB_ADMIN_STATS)
    async def admin_stats(callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        if not _is_admin(uid):
            await callback.answer(msg.TXT_ADMIN_DENIED, show_alert=True)
            return
        st = await get_sales_stats()
        text = (
            "📊 Статистика\n\n"
            f"Пользователей: {st.users_total}\n\n"
            "Продажи за сегодня:\n"
            f"MINI: {st.mini_today}\nSMART: {st.smart_today}\nULTRA: {st.ultra_today}\n\n"
            "Продажи за всё время:\n"
            f"MINI: {st.mini_all}\nSMART: {st.smart_all}\nULTRA: {st.ultra_all}\n\n"
            f"Выручка всего: {st.revenue_rub_total / 100:.2f} ₽ и {st.revenue_xtr_total} ⭐"
        )
        logger.info("admin_stats admin_id=%s", uid)
        await callback.message.answer(text)
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_ADMIN_BROADCAST)
    async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
        uid = callback.from_user.id
        if not _is_admin(uid):
            await callback.answer(msg.TXT_ADMIN_DENIED, show_alert=True)
            return
        await state.set_state(UserFlow.waiting_admin_broadcast)
        logger.info("admin_broadcast_start admin_id=%s", uid)
        await callback.message.answer(msg.TXT_ADMIN_BROADCAST_PROMPT)
        await callback.answer()

    @dp.message(UserFlow.waiting_admin_broadcast, Command("cancel"))
    async def admin_broadcast_cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Рассылка отменена.")

    @dp.message(UserFlow.waiting_admin_broadcast, F.text | F.photo)
    async def admin_broadcast_send(message: Message, state: FSMContext) -> None:
        uid = message.from_user.id
        if not _is_admin(uid):
            await message.answer(msg.TXT_ADMIN_DENIED)
            return
        user_ids = await list_all_user_ids()
        ok = 0
        fail = 0
        text = (message.text or "").strip()
        caption = (message.caption or "").strip()
        photo = message.photo[-1] if message.photo else None
        for target in user_ids:
            try:
                if photo is not None:
                    await bot.send_photo(target, photo.file_id, caption=caption or None)
                else:
                    await bot.send_message(target, text)
                ok += 1
            except Exception:
                fail += 1
        logger.info("admin_broadcast_done admin_id=%s delivered=%s failed=%s", uid, ok, fail)
        await state.clear()
        await message.answer(msg.TXT_ADMIN_BROADCAST_DONE.format(ok=ok, fail=fail))

    @dp.message(F.text == msg.MAIN_MENU_BUTTONS[0])
    async def about_bot(message: Message) -> None:
        await message.answer(msg.TXT_ABOUT_BOT)

    @dp.message(F.text == msg.MAIN_MENU_BUTTONS[1])
    async def open_create_menu(message: Message) -> None:
        await message.answer(msg.TXT_SELECT_TOOL, reply_markup=create_menu())

    @dp.callback_query(F.data == msg.CB_BACK_CREATE)
    async def back_create(callback: CallbackQuery) -> None:
        await callback.message.answer(msg.TXT_SELECT_TOOL, reply_markup=create_menu())
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_CREATE_TEXT)
    async def create_text_hint(callback: CallbackQuery) -> None:
        await callback.message.answer(msg.TXT_CREATE_TEXT_HINT)
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_CREATE_IMAGE)
    async def create_image_menu(callback: CallbackQuery) -> None:
        await callback.message.answer(msg.TXT_IMAGE_INTRO, reply_markup=image_model_menu())
        await callback.answer()

    @dp.callback_query(F.data.startswith(msg.CB_IMG_PREFIX))
    async def pick_image_model(callback: CallbackQuery, state: FSMContext) -> None:
        mid = callback.data[len(msg.CB_IMG_PREFIX) :]
        if mid not in msg.IMAGE_MODEL_IDS:
            await callback.answer(msg.TXT_UNKNOWN_IMAGE_MODEL, show_alert=True)
            return
        label = next(lbl for lbl, i in msg.IMAGE_MODELS if i == mid)
        await state.update_data(image_model_id=mid, image_model_label=label)
        await state.set_state(UserFlow.waiting_for_photo)
        await callback.message.answer(msg.TXT_CREATE_IMAGE_AFTER_MODEL)
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_CREATE_ANIMATE)
    async def create_animate_start(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.message.answer(msg.TXT_CREATE_ANIMATE_HINT)
        await state.set_state(UserFlow.waiting_for_animate)
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_CREATE_VIDEO)
    async def create_video_start(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.message.answer(msg.TXT_CREATE_VIDEO_HINT)
        await state.set_state(UserFlow.waiting_for_video)
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_CREATE_MUSIC)
    async def create_music_start(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.message.answer(msg.TXT_CREATE_MUSIC_HINT)
        await state.set_state(UserFlow.waiting_for_music)
        await callback.answer()

    @dp.callback_query(F.data == msg.CB_GEN_IMAGE_PROMPT)
    async def gen_image_prompt_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(UserFlow.waiting_for_image_prompt)
        await callback.message.answer(msg.TXT_GEN_IMAGE_PROMPT_HINT)
        await callback.answer()

    @dp.message(UserFlow.waiting_for_image_prompt, F.text)
    async def gen_image_prompt_done(message: Message, state: FSMContext) -> None:
        user_text = (message.text or "").strip()
        uid = message.from_user.id
        if not user_text:
            await message.answer(msg.TXT_GEN_IMAGE_PROMPT_NEED_TEXT)
            return
        await bot.send_chat_action(message.chat.id, "typing")
        result = await run_image_prompt_turn(settings, uid, user_text)
        if result.outcome is ImagePromptOutcome.INSUFFICIENT_BALANCE:
            await message.answer(
                msg.TXT_INSUFFICIENT_BALANCE,
                reply_markup=paycat.shop_packages_keyboard(),
            )
            await state.clear()
            return
        if result.outcome is ImagePromptOutcome.AI_FAILED:
            await message.answer(msg.TXT_GEN_JOB_FAILED)
            await state.clear()
            return
        await message.answer(result.assistant_text or "")
        await state.clear()

    @dp.message(UserFlow.waiting_for_image_prompt)
    async def gen_image_prompt_need_text(message: Message) -> None:
        await message.answer(msg.TXT_GEN_IMAGE_PROMPT_NEED_TEXT)

    @dp.callback_query(F.data == msg.CB_CABINET_PROMO)
    async def cabinet_promo_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(UserFlow.waiting_promo_code)
        await callback.message.answer(msg.TXT_PROMO_ASK)
        await callback.answer()

    @dp.message(UserFlow.waiting_promo_code, F.text)
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

    @dp.message(UserFlow.waiting_for_photo, F.text)
    async def photo_process(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id
        chat_id = message.chat.id
        data = await state.get_data()
        label = data.get("image_model_label", "модель")
        prompt = (message.text or "").strip()
        pr = await run_photo_generation_turn(settings, bot, chat_id, user_id, label, prompt)
        if pr.outcome is PhotoGenOutcome.NEED_PROMPT:
            await message.answer(msg.TXT_GEN_IMAGE_PROMPT_NEED_TEXT)
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

    @dp.message(UserFlow.waiting_for_photo)
    async def photo_process_need_text(message: Message) -> None:
        await message.answer(msg.TXT_CREATE_IMAGE_AFTER_MODEL)

    @dp.message(UserFlow.waiting_for_video, F.text)
    async def video_process(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id
        prompt = (message.text or "").strip()
        vr = await run_video_generation_turn(settings, bot, message.chat.id, user_id, prompt)
        if vr.outcome is VideoGenOutcome.NEED_PROMPT:
            await message.answer(msg.TXT_CREATE_VIDEO_HINT)
            return
        if vr.outcome is VideoGenOutcome.FORBIDDEN_BY_TARIFF:
            deny_text = msg.TXT_UPGRADE_TO_SMART if vr.upgrade_to == "smart" else msg.TXT_UPGRADE_TO_ULTRA
            await message.answer(deny_text, reply_markup=paycat.shop_packages_keyboard())
            await state.clear()
            return
        if vr.outcome is VideoGenOutcome.INSUFFICIENT_BALANCE:
            await message.answer(
                msg.TXT_INSUFFICIENT_BALANCE,
                reply_markup=paycat.shop_packages_keyboard(),
            )
            await state.clear()
            return
        await message.answer(msg.TXT_GEN_STATUS_ACCEPTED)
        if vr.vip_priority:
            await message.answer(msg.TXT_GEN_STATUS_VIP)
        await state.clear()

    @dp.message(UserFlow.waiting_for_video)
    async def video_need_text(message: Message) -> None:
        await message.answer(msg.TXT_CREATE_VIDEO_HINT)

    @dp.message(UserFlow.waiting_for_animate, F.photo)
    async def animate_process(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id
        ar = await run_animate_generation_turn(settings, bot, message.chat.id, user_id)
        if ar.outcome is AnimateGenOutcome.FORBIDDEN_BY_TARIFF:
            await message.answer(msg.TXT_UPGRADE_TO_ULTRA, reply_markup=paycat.shop_packages_keyboard())
            await state.clear()
            return
        if ar.outcome is AnimateGenOutcome.INSUFFICIENT_BALANCE:
            await message.answer(
                msg.TXT_INSUFFICIENT_BALANCE,
                reply_markup=paycat.shop_packages_keyboard(),
            )
            await state.clear()
            return
        await message.answer(msg.TXT_GEN_STATUS_ACCEPTED)
        await state.clear()

    @dp.message(UserFlow.waiting_for_animate)
    async def animate_need_photo(message: Message) -> None:
        await message.answer(msg.TXT_CREATE_ANIMATE_HINT)

    @dp.message(UserFlow.waiting_for_music, F.text)
    async def music_process(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id
        style_hint = (message.text or "").strip()
        mr = await run_music_generation_turn(settings, bot, message.chat.id, user_id, style_hint)
        if mr.outcome is MusicGenOutcome.NEED_HINT:
            await message.answer(msg.TXT_CREATE_MUSIC_HINT)
            return
        if mr.outcome is MusicGenOutcome.FORBIDDEN_BY_TARIFF:
            deny_text = msg.TXT_ACCESS_SMART_PLUS if mr.upgrade_to == "smart" else msg.TXT_UPGRADE_TO_ULTRA
            await message.answer(deny_text, reply_markup=paycat.shop_packages_keyboard())
            await state.clear()
            return
        if mr.outcome is MusicGenOutcome.INSUFFICIENT_BALANCE:
            await message.answer(
                msg.TXT_INSUFFICIENT_BALANCE,
                reply_markup=paycat.shop_packages_keyboard(),
            )
            await state.clear()
            return
        await message.answer(msg.TXT_GEN_STATUS_ACCEPTED)
        await state.clear()

    @dp.message(UserFlow.waiting_for_music)
    async def music_need_text(message: Message) -> None:
        await message.answer(msg.TXT_CREATE_MUSIC_HINT)

    @dp.message(F.text == msg.MAIN_MENU_BUTTONS[2])
    async def show_cabinet(message: Message) -> None:
        view = await build_cabinet_view(settings, message.from_user.id)
        await message.answer(view.text, reply_markup=cabinet_keyboard())

    @dp.message(F.text == msg.MAIN_MENU_BUTTONS[3])
    async def show_tariffs(message: Message) -> None:
        await message.answer(build_tariffs_entry_text(), reply_markup=paycat.shop_packages_keyboard())

    @dp.callback_query(F.data.startswith(paycat.CB_PAY_PKG_PREFIX))
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

    @dp.callback_query(F.data.startswith(paycat.CB_PAY_METHOD_PREFIX))
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

    @dp.pre_checkout_query()
    async def pre_checkout_accept(query: PreCheckoutQuery) -> None:
        await query.answer(ok=True)

    @dp.message(F.successful_payment)
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
        await message.answer(msg.TXT_PAYMENT_SUCCESS.format(amount=pay.energy_credited))
        logger.info(
            "payment_success user_id=%s energy=%s tariff=%s",
            message.from_user.id,
            pay.energy_credited,
            pay.tariff_activated or "unknown",
        )
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(
                    admin_id,
                    (
                        "💰 Новый платеж\n"
                        f"user_id: {message.from_user.id}\n"
                        f"Тариф: {pay.tariff_activated or 'unknown'}\n"
                        f"Начислено: {pay.energy_credited} ⚡"
                    ),
                )
            except Exception:
                logger.exception("failed_send_admin_payment_notice admin_id=%s", admin_id)

    @dp.message(F.text == msg.MAIN_MENU_BUTTONS[4])
    async def support_info(message: Message) -> None:
        sb = settings.support_bot_username.lstrip("@")
        await message.answer(
            msg.TXT_SUPPORT_FAQ.format(support_bot=sb),
            reply_markup=support_menu(),
        )

    result_cbs = (
        msg.CB_RESULT_ANIMATE,
        msg.CB_RESULT_REPEAT_PHOTO,
        msg.CB_RESULT_HD_PRO,
        msg.CB_RESULT_PREMIUM,
        msg.CB_RESULT_GALLERY,
        msg.CB_RESULT_MP3,
        msg.CB_RESULT_EDIT_LYRICS,
    )

    @dp.callback_query(F.data.in_(result_cbs))
    async def result_buttons_stub(callback: CallbackQuery) -> None:
        await callback.answer(msg.TXT_STUB_BUTTON)

    @dp.callback_query(F.data == msg.CB_SERVICE_RULES)
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

    @dp.callback_query(F.data == msg.CB_BACK_MAIN)
    async def back_main(callback: CallbackQuery) -> None:
        await callback.message.answer(msg.TXT_BACK_TO_MAIN, reply_markup=main_menu())
        await callback.answer()

    @dp.message(StateFilter(None), F.text.lower().in_(msg.EASTER_THANKS_TRIGGERS))
    async def easter_thanks(message: Message) -> None:
        await message.answer(random.choice(msg.EASTER_THANKS_REPLIES))

    @dp.message(
        StateFilter(None),
        F.text,
        ~F.text.startswith("/"),
        ~F.text.in_(set(msg.MAIN_MENU_BUTTONS)),
    )
    async def chat_handler(message: Message) -> None:
        uid = message.from_user.id
        raw = (message.text or "")[: settings.chat_max_message_chars]

        async def _typing() -> None:
            await bot.send_chat_action(message.chat.id, "typing")

        stream_cb = create_throttled_stream_reply(message, bot, settings) if settings.telegram_chat_streaming else None
        result = await run_chat_turn(settings, uid, raw, send_typing=_typing, stream_callback=stream_cb)
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
            await message.answer(
                msg.TXT_INSUFFICIENT_BALANCE,
                reply_markup=paycat.shop_packages_keyboard(),
            )
            return
        if result.outcome is ChatTurnOutcome.DAILY_LIMIT_EXCEEDED:
            await message.answer(msg.TXT_CHAT_DAILY_LIMIT, reply_markup=paycat.shop_packages_keyboard())
            return
        await message.answer(msg.TXT_GEN_JOB_FAILED)

    return bot, dp


async def run_telegram() -> None:
    setup_logging(settings)
    if not settings.tg_token:
        raise RuntimeError("Задайте TG_TOKEN в .env")
    if not settings.openrouter_key:
        raise RuntimeError("Задайте OPENROUTER_API_KEY в .env")
    await init_db(settings.promo_seeds)
    await start_dialog_write_worker()
    bot, dp = build_dispatcher()
    print(f"{settings.bot_name} telegram: polling started.")
    await dp.start_polling(bot)
