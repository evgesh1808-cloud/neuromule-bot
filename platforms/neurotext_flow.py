"""Меню «📝 Нейротекст» внутри «🎨 Создать» — премиум UX."""

from __future__ import annotations

import random

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from content import messages as msg
from platforms.telegram_states import UserFlow


async def ensure_neurotext_waiting_state(state: FSMContext) -> None:
    """Вход в «Нейротекст»: FSM должен ждать текст/цитату, иначе сработает свободный чат без quote."""
    data = await state.get_data()
    if not data.get("text_role"):
        await state.update_data(text_role="standard")
    await state.set_state(UserFlow.waiting_for_text_prompt)
from services.use_cases.neurotext_turn import (
    NeurotextRoleOutcome,
    build_neurotext_intro,
    get_role_availability_map,
    validate_text_role_pick,
)
from content.messages import MULE_STATIC_EXAMPLES


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
    return str(data.get("text_role") or "standard")


async def neurotext_role_keyboard(user_id: int, active_role_id: str) -> InlineKeyboardMarkup:
    avail_map = await get_role_availability_map(user_id)
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for label, role_id in msg.TEXT_ROLES:
        a = avail_map.get(role_id)
        prefix = ""
        suffix = ""
        if a and a.locked:
            prefix = "🔒 "
        if role_id == active_role_id and not (a and a.locked):
            suffix = " ✅"
        text = f"{prefix}{label}{suffix}"
        pair.append(InlineKeyboardButton(text=text, callback_data=f"{msg.CB_TEXT_ROLE_PREFIX}{role_id}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([
        InlineKeyboardButton(text=msg.TXT_NEUROTEXT_CLEAR_BTN, callback_data=msg.CB_CLEAR_CONTEXT),
    ])
    rows.append([
        InlineKeyboardButton(text=msg.TXT_BACK_TO_TOOLS, callback_data=msg.CB_BACK_CREATE),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
        InlineKeyboardButton(text=msg.TXT_BACK_TO_TOOLS, callback_data=msg.CB_BACK_CREATE),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def handle_neurotext_role_pick(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    tariffs_keyboard,
) -> None:
    """Клик по роли: переключаем или показываем upgrade-карточку."""
    await callback.answer()
    role_id = (callback.data or "").removeprefix(msg.CB_TEXT_ROLE_PREFIX)
    pick = await validate_text_role_pick(callback.from_user.id, role_id)

    if pick.outcome is NeurotextRoleOutcome.UNKNOWN_ROLE:
        await callback.answer("Неизвестный режим.", show_alert=True)
        return

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
        return

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
        return

    await state.update_data(text_role=pick.role_id)
    await state.set_state(UserFlow.waiting_for_text_prompt)

    if pick.outcome is NeurotextRoleOutcome.OK_VIA_CRYSTALS and callback.message:
        await callback.message.answer(
            f"💎 <b>Роль «{pick.role_label}» активирована за Кристаллы</b>\n"
            f"Каждое сообщение спишет <b>{pick.crystal_cost} 💎</b>. Пиши задачу одним сообщением.",
            parse_mode=ParseMode.HTML,
        )

    await open_neurotext_from_callback(callback, state)


async def handle_clear_context(callback: CallbackQuery, state: FSMContext) -> None:
    """🧹 Новый диалог — очищает ТОЛЬКО историю чата.

    По ТЗ NeuroMule 🐎⚡️ ИИ-Память (``persistent_memory``) НЕ стирается этой
    кнопкой — она доступна только из раздела «🧠 Моя память» в ЛК.
    """
    from services.repository import clear_user_dialog

    await callback.answer("Контекст очищен")
    await clear_user_dialog(callback.from_user.id)
    data = await state.get_data()
    role_id = str(data.get("text_role") or "standard")
    await state.set_state(UserFlow.waiting_for_text_prompt)
    await state.update_data(text_role=role_id)
    if callback.message:
        await callback.message.answer(msg.TXT_NEUROTEXT_CLEAR_DONE, parse_mode=ParseMode.HTML)
        await open_neurotext_from_callback(callback, state)
