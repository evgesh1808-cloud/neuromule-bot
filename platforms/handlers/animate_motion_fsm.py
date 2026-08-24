"""FSM конструктор движений перед оживлением фото (Image-to-Video)."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from config import settings
from content import messages as msg
from platforms.handlers import deps
from platforms.telegram_states import AnimateMotionStates
from services import payments_catalog as paycat
from services.animate_motion import (
    MOTION_CHOICE_MAP,
    PET_MOTION_CHOICE_MAP,
    AnimateParticipants,
    build_motion_draft_from_choices,
    resolve_animate_participants,
)
from services.animate_video_lock import is_animate_video_locked
from services.openrouter_videos import ANIMATE_DEFAULT_PROMPT, expand_motion_prompt_with_gpt
from services.repository import get_user_row
from services.tariffs import can_use_animate, normalize_tariff
from services.use_cases.animate_generation_turn import AnimateGenOutcome, run_animate_generation_turn

logger = logging.getLogger(__name__)

router = Router()

FSM_FILE_ID = "animate_file_id"
FSM_CHAT_ID = "animate_chat_id"
FSM_PEOPLE = "animate_people"
FSM_PET = "animate_pet"
FSM_STEP = "animate_step"
FSM_CHOICES = "animate_motion_choices"
FSM_AWAIT_PET = "animate_await_pet"


def _person_motion_keyboard(ref_index: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for code, (label, _motion) in MOTION_CHOICE_MAP.items():
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{msg.CB_ANIMATE_MOTION_PREFIX}{code}:{ref_index}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=msg.BTN_ANIMATE_MOTION_SKIP,
                callback_data=msg.CB_ANIMATE_MOTION_DEFAULT,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pet_motion_keyboard(ref_index: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for code, (label, _motion) in PET_MOTION_CHOICE_MAP.items():
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{msg.CB_ANIMATE_PET_MOTION_PREFIX}{code}:{ref_index}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=msg.BTN_ANIMATE_MOTION_SKIP,
                callback_data=msg.CB_ANIMATE_MOTION_DEFAULT,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _participants_to_fsm(participants: AnimateParticipants) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    people = [
        {
            "ref_index": p.ref_index,
            "display_label": p.display_label,
            "role_key": p.role_key,
        }
        for p in participants.people
    ]
    pet = None
    if participants.pet is not None:
        pet = {
            "ref_index": participants.pet.ref_index,
            "display_label": participants.pet.display_label,
            "role_key": participants.pet.role_key,
        }
    return people, pet


async def _balance_ok_for_animate(user_id: int) -> bool:
    min_cost = int(getattr(settings, "cost_animate", 20) or 20)
    row = await get_user_row(user_id)
    return int(row.crystals or 0) >= min_cost


async def _tariff_allows_animate(user_id: int) -> bool:
    row = await get_user_row(user_id)
    return can_use_animate(normalize_tariff(row.tariff))


async def start_animate_motion_survey(
    *,
    user_id: int,
    chat_id: int,
    file_id: str,
    state: FSMContext,
    session: object | None,
) -> AnimateGenOutcome | None:
    """
    Старт FSM опроса. Возвращает outcome при немедленном отказе, иначе None.
    """
    if await is_animate_video_locked(user_id):
        return AnimateGenOutcome.ALREADY_GENERATING

    if not await _tariff_allows_animate(user_id):
        return AnimateGenOutcome.FORBIDDEN_BY_TARIFF

    if not await _balance_ok_for_animate(user_id):
        return AnimateGenOutcome.INSUFFICIENT_BALANCE

    participants = resolve_animate_participants(session)  # type: ignore[arg-type]
    people, pet = _participants_to_fsm(participants)

    await state.clear()
    await state.set_state(AnimateMotionStates.choosing_motion)
    await state.update_data(
        {
            FSM_FILE_ID: file_id,
            FSM_CHAT_ID: chat_id,
            FSM_PEOPLE: people,
            FSM_PET: pet,
            FSM_STEP: 0,
            FSM_CHOICES: {},
            FSM_AWAIT_PET: False,
        }
    )
    return None


async def _send_current_motion_step(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    data = await state.get_data()
    people: list[dict[str, Any]] = list(data.get(FSM_PEOPLE) or [])
    pet: dict[str, Any] | None = data.get(FSM_PET)
    step = int(data.get(FSM_STEP) or 0)
    await_pet = bool(data.get(FSM_AWAIT_PET))

    if await_pet and pet is not None:
        await callback.message.answer(
            msg.TXT_ANIMATE_PET_ASK.format(pet=pet["display_label"]),
            reply_markup=_pet_motion_keyboard(int(pet["ref_index"])),
            parse_mode=ParseMode.HTML,
        )
        return

    if step < len(people):
        person = people[step]
        await callback.message.answer(
            msg.TXT_ANIMATE_MOTION_ASK.format(role=person["display_label"]),
            reply_markup=_person_motion_keyboard(int(person["ref_index"])),
            parse_mode=ParseMode.HTML,
        )
        return

    if pet is not None and not await_pet:
        await state.update_data({FSM_AWAIT_PET: True})
        await callback.message.answer(
            msg.TXT_ANIMATE_PET_ASK.format(pet=pet["display_label"]),
            reply_markup=_pet_motion_keyboard(int(pet["ref_index"])),
            parse_mode=ParseMode.HTML,
        )


async def _finish_animate_from_fsm(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    use_default: bool,
) -> None:
    user = callback.from_user
    if user is None or callback.message is None:
        return

    data = await state.get_data()
    file_id = str(data.get(FSM_FILE_ID) or "").strip()
    chat_id = int(data.get(FSM_CHAT_ID) or callback.message.chat.id)
    if not file_id:
        await callback.message.answer(msg.TXT_PHOTO_REFINE_EXPIRED)
        await state.clear()
        return

    if await is_animate_video_locked(user.id):
        await callback.message.answer(msg.TXT_ANIMATE_GENERATING_BUSY)
        await state.clear()
        return

    motion_prompt = ANIMATE_DEFAULT_PROMPT
    if not use_default:
        people_raw = list(data.get(FSM_PEOPLE) or [])
        pet_raw = data.get(FSM_PET)
        from services.animate_motion import AnimateParticipants, AnimatePerson, AnimatePet

        people = tuple(
            AnimatePerson(
                ref_index=int(p["ref_index"]),
                role_key=str(p["role_key"]),
                display_label=str(p["display_label"]),
            )
            for p in people_raw
        )
        pet = None
        if isinstance(pet_raw, dict):
            pet = AnimatePet(
                ref_index=int(pet_raw["ref_index"]),
                role_key=str(pet_raw["role_key"]),
                display_label=str(pet_raw["display_label"]),
            )
        participants = AnimateParticipants(people=people, pet=pet)
        choices: dict[str, str] = dict(data.get(FSM_CHOICES) or {})
        draft = build_motion_draft_from_choices(participants, choices)
        await callback.message.answer(msg.TXT_ANIMATE_MOTION_DIRECTING)
        motion_prompt = await expand_motion_prompt_with_gpt(settings, draft)

    await state.clear()
    ar = await run_animate_generation_turn(
        uid=user.id,
        telegram_file_id=file_id,
        bot=deps.bot(),
        chat_id=chat_id,
        settings=settings,
        motion_prompt=motion_prompt,
    )
    if ar.outcome is AnimateGenOutcome.SUCCESS:
        return
    if ar.outcome is AnimateGenOutcome.FORBIDDEN_BY_TARIFF:
        await callback.message.answer(
            msg.TXT_UPGRADE_TO_ULTRA,
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    if ar.outcome is AnimateGenOutcome.FREE_PREMIUM_BLOCKED:
        from platforms.telegram_utils import send_free_create_blocked

        await send_free_create_blocked(callback.message)
        return
    if ar.outcome is AnimateGenOutcome.INSUFFICIENT_BALANCE:
        await callback.message.answer(
            msg.TXT_INSUFFICIENT_BALANCE,
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    if ar.outcome is AnimateGenOutcome.ALREADY_GENERATING:
        await callback.message.answer(msg.TXT_ANIMATE_GENERATING_BUSY)


async def ask_first_animate_motion_step(message: object, state: FSMContext) -> None:
    """Первый вопрос FSM после intro."""
    data = await state.get_data()
    people: list[dict[str, Any]] = list(data.get(FSM_PEOPLE) or [])
    if not people:
        return
    person = people[0]
    await message.answer(  # type: ignore[union-attr]
        msg.TXT_ANIMATE_MOTION_ASK.format(role=person["display_label"]),
        reply_markup=_person_motion_keyboard(int(person["ref_index"])),
        parse_mode=ParseMode.HTML,
    )


async def send_animate_survey_intro(message: object, *, cost: int) -> None:
    text = msg.TXT_ANIMATE_MOTION_SURVEY_INTRO.format(cost=cost)
    await message.answer(text, parse_mode=ParseMode.HTML)  # type: ignore[union-attr]


@router.callback_query(F.data == msg.CB_ANIMATE_MOTION_DEFAULT)
async def animate_motion_use_default(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _finish_animate_from_fsm(callback, state, use_default=True)


@router.callback_query(F.data.startswith(msg.CB_ANIMATE_MOTION_PREFIX))
async def animate_person_motion_choice(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data == msg.CB_ANIMATE_MOTION_DEFAULT:
        return
    raw = (callback.data or "").strip()
    parts = raw.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    code, ref_s = parts[1], parts[2]
    motion_entry = MOTION_CHOICE_MAP.get(code)
    if motion_entry is None:
        await callback.answer()
        return

    data = await state.get_data()
    choices: dict[str, str] = dict(data.get(FSM_CHOICES) or {})
    choices[f"p:{ref_s}"] = motion_entry[1]
    step = int(data.get(FSM_STEP) or 0) + 1
    await state.update_data({FSM_CHOICES: choices, FSM_STEP: step})
    await callback.answer(motion_entry[0])

    people: list[dict[str, Any]] = list(data.get(FSM_PEOPLE) or [])
    pet = data.get(FSM_PET)
    if step >= len(people) and pet is None:
        await _finish_animate_from_fsm(callback, state, use_default=False)
        return
    if step >= len(people) and pet is not None:
        await state.update_data({FSM_AWAIT_PET: True})
    await _send_current_motion_step(callback, state)


@router.callback_query(F.data.startswith(msg.CB_ANIMATE_PET_MOTION_PREFIX))
async def animate_pet_motion_choice(callback: CallbackQuery, state: FSMContext) -> None:
    raw = (callback.data or "").strip()
    parts = raw.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    code, ref_s = parts[1], parts[2]
    motion_entry = PET_MOTION_CHOICE_MAP.get(code)
    if motion_entry is None:
        await callback.answer()
        return

    data = await state.get_data()
    choices: dict[str, str] = dict(data.get(FSM_CHOICES) or {})
    choices[f"pet:{ref_s}"] = motion_entry[1]
    await state.update_data({FSM_CHOICES: choices})
    await callback.answer(motion_entry[0])
    await _finish_animate_from_fsm(callback, state, use_default=False)
