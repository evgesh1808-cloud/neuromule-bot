"""Хэндлеры для разделов «🧠 Моя память» и «👫 DUO-доступ (ULTRA 1 мес.)» в ЛК.

ИИ-Память:
    - CB_OPEN_MEMORY — открыть карточку памяти (с текущим содержимым).
    - CB_SET_MEMORY  — попросить ввести новый текст (FSM `waiting_for_memory`).
    - CB_CLEAR_MEMORY — стереть память.

Опция DUO (только ULTRA 1 месяц):
    - CB_OPEN_FAMILY — управление DUO (список partner-id + кнопки).
    - CB_FAMILY_ADD  — запросить ID партнёра (FSM `waiting_family_member_id`).
    - CB_FAMILY_UNLINK_PREFIX + member_id — отвязать.

Памяти сюда зашиваются только тексты + DB. Подмешивание в системный промпт
работает в ``content/chat_prompt.build_system_prompt`` (через
``repo.get_persistent_memory``).
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from content import messages as msg
from platforms.telegram_states import UserFlow
from services import family_sharing as fam
from services.repository import (
    ensure_user,
    get_persistent_memory,
    get_user_row,
    set_persistent_memory,
)

logger = logging.getLogger(__name__)

router = Router(name="memory_family")


MAX_MEMORY_CHARS = 1500


# ──────────────────────────── ИИ-Память ────────────────────────────


def _memory_keyboard(filled: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=msg.TXT_MEMORY_BTN_SET,
                callback_data=msg.CB_SET_MEMORY,
            )
        ],
    ]
    if filled:
        rows.append(
            [
                InlineKeyboardButton(
                    text=msg.TXT_MEMORY_BTN_CLEAR,
                    callback_data=msg.CB_CLEAR_MEMORY,
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=msg.TXT_MEMORY_BTN_BACK,
                callback_data=msg.CB_REFRESH_PROFILE,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == msg.CB_OPEN_MEMORY)
async def open_memory(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    uid = callback.from_user.id
    memory = await get_persistent_memory(uid)
    if memory:
        text = msg.TXT_MEMORY_INTRO_FILLED.format(memory=_html_escape(memory))
    else:
        text = msg.TXT_MEMORY_INTRO_EMPTY
    await callback.message.answer(
        text,
        reply_markup=_memory_keyboard(filled=bool(memory)),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == msg.CB_SET_MEMORY)
async def ask_memory(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(UserFlow.waiting_for_memory)
    await callback.message.answer(msg.TXT_MEMORY_PROMPT, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == msg.CB_CLEAR_MEMORY)
async def clear_memory(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await set_persistent_memory(callback.from_user.id, None)
    await callback.message.answer(msg.TXT_MEMORY_CLEARED, parse_mode=ParseMode.HTML)


@router.message(UserFlow.waiting_for_memory, F.text)
async def save_memory(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        return
    if len(text) > MAX_MEMORY_CHARS:
        await message.answer(msg.TXT_MEMORY_TOO_LONG, parse_mode=ParseMode.HTML)
        return
    await set_persistent_memory(message.from_user.id, text)
    await state.clear()
    await message.answer(msg.TXT_MEMORY_SAVED, parse_mode=ParseMode.HTML)


# ──────────────────────── Опция DUO UI ────────────────────────


def _duo_keyboard(partners: list[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=msg.TXT_DUO_BTN_ADD,
                callback_data=msg.CB_FAMILY_ADD,
            )
        ]
    ]
    for mid in partners:
        rows.append(
            [
                InlineKeyboardButton(
                    text=msg.TXT_DUO_BTN_UNLINK.format(member_id=mid),
                    callback_data=f"{msg.CB_FAMILY_UNLINK_PREFIX}{mid}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=msg.TXT_DUO_BTN_BACK,
                callback_data=msg.CB_REFRESH_PROFILE,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == msg.CB_OPEN_FAMILY)
async def open_duo_access(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    uid = callback.from_user.id
    if not await fam.is_duo_owner_eligible(uid):
        await callback.message.answer(msg.TXT_DUO_NOT_ELIGIBLE, parse_mode=ParseMode.HTML)
        return
    partners = await fam.list_duo_partners(uid)
    text = msg.TXT_DUO_INTRO.format(
        count=len(partners), limit=fam.MAX_DUO_MEMBERS
    )
    await callback.message.answer(
        text,
        reply_markup=_duo_keyboard(partners),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == msg.CB_FAMILY_ADD)
async def duo_ask_partner_id(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if not await fam.is_duo_owner_eligible(callback.from_user.id):
        await callback.message.answer(msg.TXT_DUO_NOT_ELIGIBLE, parse_mode=ParseMode.HTML)
        return
    await state.set_state(UserFlow.waiting_family_member_id)
    await callback.message.answer(msg.TXT_DUO_ADD_ASK, parse_mode=ParseMode.HTML)


@router.message(UserFlow.waiting_family_member_id, F.text)
async def duo_add_partner(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        member_id = int(raw)
    except ValueError:
        await message.answer(msg.TXT_DUO_ADD_BAD_ID, parse_mode=ParseMode.HTML)
        return
    await ensure_user(member_id)
    try:
        member = await get_user_row(member_id)
    except Exception:
        member = None
    # Грубая эвристика «незарегистрирован» — нет записи с заполненными полями.
    if member is None:
        await message.answer(msg.TXT_DUO_ADD_NOT_REGISTERED, parse_mode=ParseMode.HTML)
        return

    owner_id = message.from_user.id
    ok, err = await fam.link_duo_partner(owner_id, member_id)
    if not ok:
        await message.answer(
            msg.TXT_DUO_ADD_FAIL.get(err, f"⚠️ Ошибка: {err}"),
            parse_mode=ParseMode.HTML,
        )
        await state.clear()
        return
    await state.clear()
    await message.answer(
        msg.TXT_DUO_ADD_OK.format(member_id=member_id),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith(msg.CB_FAMILY_UNLINK_PREFIX))
async def duo_unlink_partner(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    raw = (callback.data or "").removeprefix(msg.CB_FAMILY_UNLINK_PREFIX)
    try:
        member_id = int(raw)
    except ValueError:
        return
    await fam.unlink_duo_partner(callback.from_user.id, member_id)
    await callback.message.answer(
        msg.TXT_DUO_UNLINK_OK.format(member_id=member_id),
        parse_mode=ParseMode.HTML,
    )


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
