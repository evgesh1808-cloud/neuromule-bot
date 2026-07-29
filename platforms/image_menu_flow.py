"""Меню выбора модели фото: FSM-маркер и перехват промпта без inline-кнопки."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram.enums import ParseMode
from aiogram.types import Message

from content import messages as msg
from platforms.telegram_keyboards import image_model_menu
from platforms.telegram_states import UserFlow
from services.billing.types import TariffTier
from services.repository import get_user_row

if TYPE_CHECKING:
    from aiogram.fsm.context import FSMContext

IMAGE_MODEL_MENU_PENDING_KEY = "image_model_menu_pending"
FREE_DEFAULT_IMAGE_MODEL_ID = "flux-schnell"
FREE_DEFAULT_IMAGE_MODEL_LABEL = "Flux Schnell"

# Состояния, где текст — не «промпт для фото из меню».
_BLOCKED_FSM_STATES = frozenset(
    {
        UserFlow.waiting_for_photo.state,
        UserFlow.waiting_for_video.state,
        UserFlow.waiting_for_video_prank_photo.state,
        UserFlow.waiting_for_animate.state,
        UserFlow.waiting_for_upscale_photo.state,
        UserFlow.waiting_promo_code.state,
        UserFlow.waiting_for_memory.state,
        UserFlow.waiting_family_member_id.state,
        UserFlow.waiting_advice_birth.state,
        UserFlow.waiting_hd_birth_data.state,
        UserFlow.WAITING_PARTNER_DATA.state,
    }
)


async def mark_image_model_menu_pending(state: FSMContext) -> None:
    await state.update_data(**{IMAGE_MODEL_MENU_PENDING_KEY: True})


async def clear_image_model_menu_pending(state: FSMContext) -> None:
    await state.update_data(**{IMAGE_MODEL_MENU_PENDING_KEY: False})


def is_image_model_menu_pending(data: dict) -> bool:
    return bool(data.get(IMAGE_MODEL_MENU_PENDING_KEY))


async def can_intercept_text_as_image_prompt(message: Message, state: FSMContext) -> bool:
    text = (message.text or "").strip()
    if not text:
        return False
    if text.startswith("/"):
        return False
    if text in msg.ALL_REPLY_NAV_BUTTONS:
        return False

    current = await state.get_state()
    if current == UserFlow.waiting_for_image_model_pick.state:
        return True

    data = await state.get_data()
    if not is_image_model_menu_pending(data):
        return False

    if current in _BLOCKED_FSM_STATES:
        return False

    from platforms.marketplace_audit_flow import is_marketplace_audit_context

    if is_marketplace_audit_context(current, data):
        return False

    return True


async def present_image_model_menu(
    message: Message,
    state: FSMContext,
    user_id: int,
) -> None:
    """Показать меню моделей; следующий текст — промпт (не чат)."""
    row = await get_user_row(user_id)
    tariff = TariffTier.from_db(row.tariff)
    await mark_image_model_menu_pending(state)
    await state.set_state(UserFlow.waiting_for_image_model_pick)

    await message.answer(
        msg.get_text_image_models(tariff),
        reply_markup=image_model_menu(
            tariff,
            photo_daily_count=row.photo_daily_count,
            photo_daily_date=row.photo_daily_date,
        ),
        parse_mode=ParseMode.HTML,
    )


async def handle_pending_image_menu_text(message: Message, state: FSMContext) -> None:
    """Текст после меню фото без выбора модели: FREE → Flux Schnell, иначе — подсказка."""
    from services.billing.free_tier_gates import is_free_user

    user_id = message.from_user.id
    prompt = (message.text or "").strip()

    if await is_free_user(user_id):
        await state.update_data(
            image_model_id=FREE_DEFAULT_IMAGE_MODEL_ID,
            image_model_label=FREE_DEFAULT_IMAGE_MODEL_LABEL,
        )
        await state.set_state(UserFlow.waiting_for_photo)
        await clear_image_model_menu_pending(state)
        from platforms.handlers.generation_fsm import process_photo_prompt_message

        await process_photo_prompt_message(
            message,
            state,
            model_id=FREE_DEFAULT_IMAGE_MODEL_ID,
            label=FREE_DEFAULT_IMAGE_MODEL_LABEL,
            prompt=prompt,
            auto_flux=True,
        )
        return

    await message.answer(msg.TXT_IMAGE_PICK_MODEL_FIRST, parse_mode=ParseMode.HTML)
