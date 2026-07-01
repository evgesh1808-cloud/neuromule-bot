"""Меню «📝 Нейротекст» внутри «🎨 Создать» — премиум UX."""

from __future__ import annotations

import random

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from content import messages as msg
from content.messages import MULE_STATIC_EXAMPLES
from platforms.telegram_keyboards import (
    create_lifestyle_subroles_keyboard,
    create_marketplace_audit_platform_keyboard,
    create_roles_menu_keyboard,
    create_table_subroles_keyboard,
)
from platforms.telegram_states import UserFlow
from services.use_cases.neurotext_turn import (
    NeurotextRoleOutcome,
    build_neurotext_intro,
    get_role_availability_map,
    normalize_text_role_id,
    validate_text_role_pick,
)


async def ensure_neurotext_waiting_state(state: FSMContext) -> None:
    """Вход в «Нейротекст»: FSM ждёт текст/файл; audit-состояния площадок не сбрасываем."""
    from platforms.marketplace_audit_flow import is_marketplace_audit_context

    data = await state.get_data()
    if not data.get("text_role"):
        await state.update_data(text_role="standard")
    current = await state.get_state()
    if is_marketplace_audit_context(current, data):
        return
    await state.set_state(UserFlow.waiting_for_text_prompt)


def _with_standard_example(base_text: str, active_role_id: str) -> str:
    if (active_role_id or "").strip().lower() != "standard":
        return base_text
    current_example = random.choice(MULE_STATIC_EXAMPLES)
    return (
        f"{base_text}\n\n"
        "✅ <b>Режим «Стандарт» включён.</b>\n\n"
        "Напишите всё <b>одним сообщением</b>: тему текста и желаемый тон. "
        "Мул ответит в выбранном стиле!\n\n"
        f"<i>Пример: {current_example}</i>"
    )


async def _active_role_id(state: FSMContext) -> str:
    data = await state.get_data()
    return normalize_text_role_id(str(data.get("text_role") or "standard"))


async def neurotext_role_keyboard(user_id: int, active_role_id: str) -> InlineKeyboardMarkup:
    return await create_roles_menu_keyboard(user_id, active_role_id)


async def send_neurotext_role_menu(message: Message, state: FSMContext | None = None) -> None:
    if state is not None:
        await ensure_neurotext_waiting_state(state)
    active = await _active_role_id(state) if state else "standard"
    text = await build_neurotext_intro(message.from_user.id, active)
    text = _with_standard_example(text, active)
    kb = await neurotext_role_keyboard(message.from_user.id, active)
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)


async def open_neurotext_from_callback(callback: CallbackQuery, state: FSMContext | None = None) -> None:
    if not callback.message:
        await callback.answer()
        return
    if state is not None:
        await ensure_neurotext_waiting_state(state)
    active = await _active_role_id(state) if state else "standard"
    text = await build_neurotext_intro(callback.from_user.id, active)
    text = _with_standard_example(text, active)
    kb = await neurotext_role_keyboard(callback.from_user.id, active)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            await callback.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await callback.answer()


def _upgrade_card_keyboard(tariffs_keyboard) -> InlineKeyboardMarkup:
    base = tariffs_keyboard()
    rows = list(base.inline_keyboard)
    rows.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=msg.CB_BACK_TO_TOOLS),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _handle_role_pick_locked(
    callback: CallbackQuery,
    pick,
    *,
    tariffs_keyboard,
) -> bool:
    """Обработка блокировок роли. True = выход без активации."""
    if pick.outcome is NeurotextRoleOutcome.UNKNOWN_ROLE:
        await callback.answer("Неизвестный режим.", show_alert=True)
        return True

    if pick.outcome is NeurotextRoleOutcome.SMART_REQUIRED:
        await callback.answer(
            f"⛔ Роль «{pick.role_label}» доступна только с пакета SMART. "
            "Открой «🚀 Тарифы», чтобы активировать.",
            show_alert=True,
        )
        if callback.message:
            await callback.message.answer(
                "⛔ <b>Доступ только с тарифа SMART</b>\n\n"
                "Подкаст-сценарии включаются на пакете <b>SMART</b> и выше — открой их одним кликом ниже.",
                reply_markup=_upgrade_card_keyboard(tariffs_keyboard),
                parse_mode=ParseMode.HTML,
            )
        return True

    if pick.outcome is NeurotextRoleOutcome.PREMIUM_LOCKED:
        await callback.answer(
            f"🔒 Роль «{pick.role_label}» — {pick.crystal_cost} 💎. "
            "Активируй пакет MINI или докупи Кристаллы.",
            show_alert=True,
        )
        if callback.message:
            await callback.message.answer(
                f"🔒 <b>Роль «{pick.role_label}»</b>\n\n"
                f"Стоимость: <b>{pick.crystal_cost} 💎</b> или открой пакет MINI для безлимита по ⚡.\n"
                "Можно пригласить друзей и получить Кристаллы бесплатно, либо купить пакет ниже:",
                reply_markup=_upgrade_card_keyboard(tariffs_keyboard),
                parse_mode=ParseMode.HTML,
            )
        return True
    return False


async def handle_neurotext_role_pick(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    tariffs_keyboard,
) -> None:
    """Клик по роли (``set_role:`` или legacy ``text_role:``)."""
    raw = callback.data or ""
    if raw.startswith(msg.CB_SET_ROLE_PREFIX):
        role_id = raw.removeprefix(msg.CB_SET_ROLE_PREFIX)
    else:
        role_id = raw.removeprefix(msg.CB_TEXT_ROLE_PREFIX)
    role_id = normalize_text_role_id(role_id)
    pick = await validate_text_role_pick(callback.from_user.id, role_id)

    if await _handle_role_pick_locked(callback, pick, tariffs_keyboard=tariffs_keyboard):
        return

    if pick.role_id == "table_generator":
        await state.update_data(text_role=pick.role_id)
    else:
        await state.update_data(
            text_role=pick.role_id,
            table_subrole=None,
            audit_platform=None,
        )

    if pick.role_id == "table_generator":
        await handle_show_table_subcategories(callback, state, tariffs_keyboard=tariffs_keyboard, answered=True)
        return

    await state.set_state(UserFlow.waiting_for_text_prompt)

    if pick.outcome is NeurotextRoleOutcome.OK_VIA_CRYSTALS and callback.message:
        await callback.message.answer(
            f"💎 <b>Роль «{pick.role_label}» активирована за Кристаллы</b>\n"
            f"Каждое сообщение спишет <b>{pick.crystal_cost} 💎</b>. Пиши задачу одним сообщением.",
            parse_mode=ParseMode.HTML,
        )

    await open_neurotext_from_callback(callback, state)

    if pick.role_id == "summary" and callback.message:
        from platforms.summarizer_flow import send_summary_mode_hint

        await send_summary_mode_hint(callback.message)


async def handle_show_table_subcategories(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    tariffs_keyboard=None,
    answered: bool = False,
) -> None:
    """Подменю площадок для финансового аудита (сквозная аналитика)."""
    if not answered:
        await callback.answer()
    pick = await validate_text_role_pick(callback.from_user.id, "table_generator")
    if tariffs_keyboard and await _handle_role_pick_locked(callback, pick, tariffs_keyboard=tariffs_keyboard):
        return

    await state.update_data(text_role="table_generator", table_subrole=None, audit_platform=None)

    if pick.outcome is NeurotextRoleOutcome.OK_VIA_CRYSTALS and callback.message:
        await callback.message.answer(
            f"💎 <b>Роль «{pick.role_label}» активирована за Кристаллы</b>\n"
            f"Каждое сообщение спишет <b>{pick.crystal_cost} 💎</b>.",
            parse_mode=ParseMode.HTML,
        )

    if callback.message:
        try:
            await callback.message.edit_text(
                msg.TXT_AUDIT_PLATFORM_MENU,
                reply_markup=create_marketplace_audit_platform_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await callback.message.answer(
                    msg.TXT_AUDIT_PLATFORM_MENU,
                    reply_markup=create_marketplace_audit_platform_keyboard(),
                    parse_mode=ParseMode.HTML,
                )


async def handle_show_table_subrole_menu(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Дополнительные типы отчётов (стандарт, ROI, SEO)."""
    await callback.answer()
    await state.update_data(table_subrole=None, audit_platform=None)
    if callback.message:
        try:
            await callback.message.edit_text(
                msg.TXT_TABLE_SUBROLE_MENU,
                reply_markup=create_table_subroles_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await callback.message.answer(
                    msg.TXT_TABLE_SUBROLE_MENU,
                    reply_markup=create_table_subroles_keyboard(),
                    parse_mode=ParseMode.HTML,
                )


async def handle_show_lifestyle_subcategories(callback: CallbackQuery, state: FSMContext) -> None:
    """Плавное переключение на подменю лайфстайл-ролей."""
    await callback.answer()
    active = await _active_role_id(state)
    avail = await get_role_availability_map(callback.from_user.id)
    kb = create_lifestyle_subroles_keyboard(availability=avail, active_role_id=active)
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await callback.message.answer(
                    "✨ <b>Лайфстайл & Блоги</b> — выберите роль:",
                    reply_markup=kb,
                    parse_mode=ParseMode.HTML,
                )


async def handle_back_to_roles_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Возврат из подменю в главное меню ролей."""
    await callback.answer()
    active = await _active_role_id(state)
    kb = await create_roles_menu_keyboard(callback.from_user.id, active)
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await open_neurotext_from_callback(callback, state)


async def handle_clear_context(callback: CallbackQuery, state: FSMContext) -> None:
    """🔔 Новый диалог в меню ИИ Ассистент — универсальная очистка чата, зачистка экрана и вывод VIP-интерфейса."""
    from services.dialog_platform import DIALOG_PLATFORM_TELEGRAM
    from services.repository import clear_user_dialog

    user_id = callback.from_user.id

    # ФИШКА 1: Авто-зачистка экрана (удаляем старую клавиатуру меню ИИ Ассистент)
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            pass

    # Базовый SQL-запрос: вырезаем оперативную историю сообщений сеанса из БД SQLite
    await clear_user_dialog(user_id, platform=DIALOG_PLATFORM_TELEGRAM)
    await callback.answer("Контекст Системы очищен")

    # Определяем текущую активную роль пользователя и удерживаем её в FSM
    data = await state.get_data()
    role_id = normalize_text_role_id(str(data.get("text_role") or "standard"))
    await state.set_state(UserFlow.waiting_for_text_prompt)
    await state.update_data(text_role=role_id)

    # ФИШКА 2: Создаем единую инлайн-кнопку тотального сброса ИИ-памяти
    vip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧠 Очистить долгосрочную память ИИ",
                    callback_data="clear_persistent_memory_vip",
                )
            ]
        ]
    )

    # ФИШКА 3: Разделенный, лаконичный и адаптивный премиальный микрокопирайтинг
    if role_id == "standard":
        welcome_text = (
            "🐎⚡️ <b>История чата успешно очищена.</b>\n\n"
            "<b>Отправьте новый запрос:</b>\n"
            "• Напишите ваш вопрос, задачу или код.\n"
            "• Загрузите файл (<code>.txt</code>, <code>.pdf</code>, <code>.docx</code>) для анализа.\n\n"
            "<i>Для удаления долгосрочной памяти ИИ нажмите кнопку ниже:</i>"
        )
    else:
        welcome_text = (
            "🐎⚡️ <b>Режим очищен и готов к работе!</b>\n\n"
            "Все системы сброшены в исходное состояние, а оперативная память нейронов чиста.\n\n"
            "<b>NeuroMule готов к работе:</b> отправьте текстовый запрос, задачу, код или описание, "
            "чтобы проложить новый маршрут.\n\n"
            "<i>Для удаления долгосрочной памяти ИИ нажмите кнопку ниже:</i>"
        )

    # Чистое сообщение с VIP-кнопкой (без автоматического возврата меню ролей)
    if callback.message:
        await callback.message.answer(
            text=welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=vip_kb,
        )
