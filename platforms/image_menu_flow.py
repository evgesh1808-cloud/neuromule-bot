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
# FREE без меню: длинный текст или маркеры промпта → Flux, не чат.
FREE_AUTO_IMAGE_INTERCEPT_MIN_LEN = 250

_FREE_AUTO_IMAGE_IDLE_STATES = frozenset(
    {
        None,
        UserFlow.waiting_for_text_prompt.state,
        UserFlow.waiting_for_image_model_pick.state,
    }
)

_IMAGE_PROMPT_MARKERS = (
    "9:16",
    "16:9",
    "1:1",
    "3:4",
    "4:3",
    "portrait",
    "photo",
    "фото",
    "изображ",
    "картин",
    "style:",
    "lighting",
    "background",
    "realistic",
    "реалист",
    "промпт",
    "генерац",
    "aspect",
    "flux",
    "imagen",
    "reference",
    "референс",
    "лицо",
    "face",
    "soft light",
    "cinematic",
    "кинемат",
)

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


def text_looks_like_image_prompt(text: str) -> bool:
    """Эвристика: описание сцены/кадра, а не короткий вопрос в чат."""
    body = (text or "").strip()
    if len(body) >= 400:
        return True
    low = body.lower()
    hits = sum(1 for marker in _IMAGE_PROMPT_MARKERS if marker in low)
    if hits >= 2:
        return True
    return hits >= 1 and len(body) >= 120


async def _free_tier_should_auto_intercept_image(
    message: Message,
    state: FSMContext,
    *,
    text: str,
    current: str | None,
) -> bool:
    """FREE: длинный/«картиночный» текст в idle/нейротекст → фото, не run_chat_turn."""
    from services.billing.free_tier_gates import is_free_user

    user_id = message.from_user.id if message.from_user else 0
    if not user_id:
        return False
    if current not in _FREE_AUTO_IMAGE_IDLE_STATES:
        return False
    if current in _BLOCKED_FSM_STATES:
        return False
    if len(text) < FREE_AUTO_IMAGE_INTERCEPT_MIN_LEN and not text_looks_like_image_prompt(text):
        return False
    if not await is_free_user(user_id):
        return False

    data = await state.get_data()
    role = str(data.get("text_role") or "standard").strip().lower()
    if role not in ("standard", ""):
        return False

    from platforms.marketplace_audit_flow import is_marketplace_audit_context

    if is_marketplace_audit_context(current, data):
        return False

    return True


async def route_free_text_to_flux_photo(
    message: Message,
    state: FSMContext,
    prompt: str,
    *,
    auto_flux: bool = True,
) -> None:
    """FREE → Flux Schnell + ``process_photo_prompt_message``."""
    from platforms.handlers.generation_fsm import process_photo_prompt_message

    await state.update_data(
        image_model_id=FREE_DEFAULT_IMAGE_MODEL_ID,
        image_model_label=FREE_DEFAULT_IMAGE_MODEL_LABEL,
    )
    await state.set_state(UserFlow.waiting_for_photo)
    await clear_image_model_menu_pending(state)
    await process_photo_prompt_message(
        message,
        state,
        model_id=FREE_DEFAULT_IMAGE_MODEL_ID,
        label=FREE_DEFAULT_IMAGE_MODEL_LABEL,
        prompt=prompt,
        auto_flux=auto_flux,
    )


async def try_free_photo_from_chat_overflow(
    message: Message,
    state: FSMContext,
    *,
    prompt: str,
    user_id: int,
) -> bool:
    """Последний шанс: CONTEXT_TOO_LARGE на FREE → попробовать Flux вместо ошибки чата."""
    from services.billing.free_tier_gates import is_free_user

    body = (prompt or "").strip()
    if not body or not await is_free_user(user_id):
        return False

    data = await state.get_data()
    role = str(data.get("text_role") or "standard").strip().lower()
    if role not in ("standard", ""):
        return False

    if len(body) < FREE_AUTO_IMAGE_INTERCEPT_MIN_LEN and not text_looks_like_image_prompt(body):
        return False

    await route_free_text_to_flux_photo(message, state, body)
    return True


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

    if await _free_tier_should_auto_intercept_image(
        message,
        state,
        text=text,
        current=current,
    ):
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
        await route_free_text_to_flux_photo(message, state, prompt)
        return

    await message.answer(msg.TXT_IMAGE_PICK_MODEL_FIRST, parse_mode=ParseMode.HTML)
