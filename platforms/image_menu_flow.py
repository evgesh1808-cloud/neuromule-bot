"""Меню выбора модели фото: FSM-маркер и перехват промпта без inline-кнопки."""

from __future__ import annotations

import logging
import re
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

logger = logging.getLogger(__name__)

IMAGE_MODEL_MENU_PENDING_KEY = "image_model_menu_pending"
FREE_DEFAULT_IMAGE_MODEL_LABEL = "Flux FREE"
# FREE без меню: длинный текст или маркеры промпта → Flux FREE, не чат.
FREE_AUTO_IMAGE_INTERCEPT_MIN_LEN = 250

# WebApp Studio: «Генерация логотипа студии (Формат 1:1):<prompt>»
_STUDIO_PROMPT_PREFIX_RE = re.compile(
    r"^Генерация\s+.+?\(\s*Формат\s+[0-9]+\s*:\s*[0-9]+\s*\)\s*:?\s*",
    re.IGNORECASE | re.DOTALL,
)


def _free_default_image_model_id() -> str:
    from services.billing.image_pipeline import free_tier_image_model

    return free_tier_image_model()

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
    "logo",
    "логотип",
    "esports",
    "gaming",
    "3d render",
    "masterpiece",
    "8k",
    "cyberpunk",
    "emblem",
)


def normalize_image_prompt_text(text: str) -> str:
    """Убирает префикс WebApp Studio и лишние пробелы."""
    body = (text or "").strip()
    if not body:
        return ""
    stripped = _STUDIO_PROMPT_PREFIX_RE.sub("", body, count=1).strip()
    return stripped or body


def looks_like_studio_image_request(text: str) -> bool:
    """Промпт из Mini App Studio (логотип / обложка / формат кадра)."""
    body = (text or "").strip()
    if not body:
        return False
    if _STUDIO_PROMPT_PREFIX_RE.match(body):
        return True
    low = body[:160].lower()
    return "генерация" in low and "формат" in low and ":" in body[:160]


def message_looks_like_photo_prompt(text: str) -> bool:
    """Синхронная эвристика для outer throttle (FSM там недоступен)."""
    body = (text or "").strip()
    if not body or body.startswith("/"):
        return False
    if looks_like_studio_image_request(body):
        return True
    return text_looks_like_image_prompt(body)

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


def _mark_photo_flow_for_message(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    from platforms.telegram_throttling import mark_photo_flow

    mark_photo_flow(user.id)


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
    """FREE: авто-перехват текста в фото отключён (раздел только на платных тарифах)."""
    return False


async def route_free_text_to_flux_photo(
    message: Message,
    state: FSMContext,
    prompt: str,
    *,
    auto_flux: bool = True,
) -> None:
    """FREE → бесплатное фото дня (каскад провайдеров)."""
    from platforms.handlers.generation_fsm import process_photo_prompt_message

    _mark_photo_flow_for_message(message)
    model_id = _free_default_image_model_id()
    await state.update_data(
        image_model_id=model_id,
        image_model_label=FREE_DEFAULT_IMAGE_MODEL_LABEL,
    )
    await state.set_state(UserFlow.waiting_for_photo)
    await clear_image_model_menu_pending(state)
    await process_photo_prompt_message(
        message,
        state,
        model_id=model_id,
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
    """FREE: бесплатное фото из чата отключено."""
    return False


async def can_intercept_text_as_image_prompt(message: Message, state: FSMContext) -> bool:
    text = (message.text or "").strip()
    if not text:
        return False
    if text.startswith("/"):
        return False
    if text in msg.ALL_REPLY_NAV_BUTTONS:
        return False
    from platforms.telegram_utils import is_reply_nav_button_text

    if is_reply_nav_button_text(text):
        return False

    current = await state.get_state()

    if looks_like_studio_image_request(text) and current in (
        None,
        UserFlow.waiting_for_text_prompt.state,
        UserFlow.waiting_for_image_model_pick.state,
        UserFlow.waiting_for_photo.state,
    ):
        return True

    if current == UserFlow.waiting_for_image_model_pick.state:
        return True

    data = await state.get_data()
    model_id = str(data.get("image_model_id") or "").strip()
    # Модель уже выбрана (Flux FREE / inline), но FSM state мог не сохраниться в Redis.
    if model_id and current in (
        None,
        UserFlow.waiting_for_photo.state,
        UserFlow.waiting_for_image_model_pick.state,
    ):
        return True

    if await _free_tier_should_auto_intercept_image(
        message,
        state,
        text=text,
        current=current,
    ):
        return True

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
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
    from platforms.telegram_utils import send_free_create_blocked
    from services.billing.daily_quotas import get_free_photo_snapshot, quota_day
    from services.billing.free_tier_gates import is_free_user

    if await is_free_user(user_id):
        await send_free_create_blocked(message)
        return

    # Маркер FSM + in-memory photo-flow до SQLite/Telegram.
    try:
        await mark_image_model_menu_pending(state)
        await state.set_state(UserFlow.waiting_for_image_model_pick)
    except Exception:
        logger.warning(
            "present_image_model_menu: early FSM not saved uid=%s",
            user_id,
            exc_info=True,
        )
    from platforms.telegram_throttling import mark_photo_flow

    mark_photo_flow(user_id)

    tariff = TariffTier.FREE
    snap_used = 0
    snap_day: str | None = quota_day()

    try:
        row = await get_user_row(user_id)
        tariff = TariffTier.from_db(row.tariff)
    except Exception:
        logger.exception("present_image_model_menu: get_user_row failed uid=%s", user_id)

    try:
        snap = await get_free_photo_snapshot(user_id)
        snap_used, snap_day = snap.used, snap.day
    except Exception:
        logger.exception("present_image_model_menu: quota snapshot failed uid=%s", user_id)

    menu_text = msg.get_text_image_models(tariff)
    try:
        markup = image_model_menu(
            tariff,
            free_photo_used=snap_used,
            free_photo_day=snap_day,
        )
    except Exception:
        logger.exception("present_image_model_menu: keyboard build failed uid=%s", user_id)
        try:
            await message.answer(msg.TXT_IMAGE_MENU_OPEN_FAILED)
        except Exception:
            logger.debug("present_image_model_menu fallback answer failed", exc_info=True)
        return

    sent = False
    try:
        await message.answer(
            menu_text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
        sent = True
    except TelegramBadRequest as exc:
        logger.warning(
            "present_image_model_menu HTML answer failed uid=%s: %s",
            user_id,
            exc,
        )
        try:
            await message.answer(menu_text, reply_markup=markup)
            sent = True
        except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError) as exc2:
            logger.error(
                "present_image_model_menu plain answer failed uid=%s: %s",
                user_id,
                exc2,
            )
        except Exception:
            logger.exception("present_image_model_menu plain answer unexpected uid=%s", user_id)
    except (TelegramForbiddenError, TelegramNetworkError) as exc:
        logger.error("present_image_model_menu send failed uid=%s: %s", user_id, exc)
    except Exception:
        logger.exception("present_image_model_menu send unexpected uid=%s", user_id)

    if not sent:
        try:
            await message.answer(msg.TXT_IMAGE_MENU_OPEN_FAILED)
        except Exception:
            logger.debug("present_image_model_menu fallback answer failed", exc_info=True)
        return

    from services.billing.image_pipeline import free_tier_image_model

    preselect_free = tariff is TariffTier.FREE
    try:
        await mark_image_model_menu_pending(state)
        if preselect_free:
            await state.update_data(
                image_model_id=free_tier_image_model(),
                image_model_label=FREE_DEFAULT_IMAGE_MODEL_LABEL,
            )
            await state.set_state(UserFlow.waiting_for_photo)
        else:
            await state.set_state(UserFlow.waiting_for_image_model_pick)
    except Exception:
        # Меню уже в чате — FSM/Redis не должен блокировать UX.
        logger.warning(
            "present_image_model_menu: FSM state not saved uid=%s (menu was sent)",
            user_id,
            exc_info=True,
        )


async def handle_pending_image_menu_text(message: Message, state: FSMContext) -> None:
    """Текст после меню фото без выбора модели: FREE → Flux FREE, иначе — подсказка."""
    from platforms.telegram_utils import is_image_reply_button_text, is_reply_nav_button_text
    from services.billing.free_tier_gates import is_free_user

    user_id = message.from_user.id
    raw_text = (message.text or "").strip()
    if is_image_reply_button_text(raw_text):
        await present_image_model_menu(message, state, user_id)
        return
    if is_reply_nav_button_text(raw_text):
        return

    prompt = normalize_image_prompt_text(raw_text)
    if not prompt:
        await message.answer(msg.TXT_CREATE_IMAGE_AFTER_MODEL)
        return

    data = await state.get_data()
    model_id = str(data.get("image_model_id") or "").strip()
    if model_id:
        from platforms.handlers.generation_fsm import process_photo_prompt_message

        _mark_photo_flow_for_message(message)
        label = str(data.get("image_model_label") or FREE_DEFAULT_IMAGE_MODEL_LABEL).strip()
        await clear_image_model_menu_pending(state)
        await state.set_state(UserFlow.waiting_for_photo)
        await process_photo_prompt_message(
            message,
            state,
            model_id=model_id,
            label=label,
            prompt=prompt,
        )
        return

    if await is_free_user(user_id):
        from platforms.telegram_utils import send_free_create_blocked

        await send_free_create_blocked(message)
        return

    await message.answer(msg.TXT_IMAGE_PICK_MODEL_FIRST, parse_mode=ParseMode.HTML)


async def handle_free_photo_with_reference(
    message: Message,
    state: FSMContext,
    *,
    prompt: str,
    file_id: str,
) -> None:
    """Image-to-image на FREE отключён."""
    from platforms.telegram_utils import send_free_create_blocked

    await send_free_create_blocked(message)
