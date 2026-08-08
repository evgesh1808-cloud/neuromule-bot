"""Throttling middleware (anti-fraud, anti-double-click) для aiogram 3.

Защищает кошелёк и очередь генераций от:
* двойного клика по кнопкам генерации (typical "lag-rage" пользователя);
* флуда от ботов и накрутчиков на тарифе FREE;
* Race Condition'ов между параллельными запросами, которые могли бы
  списать ресурсы дважды до завершения первой транзакции.

Архитектура:
* Per-user in-memory rate-limiter с гранулярностью «1 событие в N секунд».
* Применяется к ``CallbackQuery`` (кнопки) и ``Message`` (текстовые
  команды и FSM-инпуты).
* При попадании в окно cooldown: для callback'а вызывается
  ``callback.answer(text, show_alert=False)`` — пользователь видит
  ненавязчивую плашку, анимация часиков мгновенно гаснет, основной
  хэндлер НЕ запускается.
* В лог пишется `INFO` с user_id и фактом срабатывания — нужно для
  мониторинга накрутчиков и тюнинга порогов.

Photo-flow bypass:
* ``ThrottlingMiddleware`` — outer_middleware: ``FSMContext`` ещё не в
  ``data``, поэтому проверка FSM здесь бесполезна. Для меню «Изображение»
  / Flux FREE используем in-memory ``mark_photo_flow(user_id)``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Final

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from content import messages as msg
from services import metrics

logger = logging.getLogger(__name__)


DEFAULT_COOLDOWN_SEC: Final = 2.0
PHOTO_FLOW_BYPASS_SEC: Final = 120.0
DEFAULT_ALERT_TEXT: Final = (
    "⏳ Не спеши! NeuroMule обрабатывает твой прошлый запрос."
)

# Дешёвые / safe-операции — не троттлим, чтобы не раздражать UX.
WHITELISTED_CALLBACK_DATA: Final[frozenset[str]] = frozenset(
    {
        msg.CB_ACCEPT_LEGAL_TOS,
        msg.CB_CHECK_SUBSCRIPTION,
        msg.CB_RECHECK_SUBSCRIPTION,
        msg.CB_REFRESH_PROFILE,
        msg.CB_TOGGLE_SUGGESTED_REPLIES,
        msg.CB_GALLERY_CANCEL,
        msg.CB_GALLERY_CONFIRM,
        msg.CB_SHARE_TO_GALLERY,
    }
)

# Per-user последний tick (monotonic seconds).
_LAST_CALL_AT: dict[int, float] = {}
# In-memory photo-flow bypass (outer middleware не видит FSM).
_PHOTO_FLOW_UNTIL: dict[int, float] = {}


def _user_id_of(event: TelegramObject) -> int | None:
    user = getattr(event, "from_user", None)
    if user is None:
        return None
    return int(getattr(user, "id", 0)) or None


def mark_photo_flow(user_id: int, *, ttl_sec: float = PHOTO_FLOW_BYPASS_SEC) -> None:
    """Разрешить photo-промпты без cooldown (меню / Flux FREE / waiting_for_photo)."""
    uid = int(user_id)
    _PHOTO_FLOW_UNTIL[uid] = time.monotonic() + float(ttl_sec)
    reset_throttle(uid)


def clear_photo_flow(user_id: int) -> None:
    _PHOTO_FLOW_UNTIL.pop(int(user_id), None)


def is_photo_flow_active(user_id: int) -> bool:
    until = _PHOTO_FLOW_UNTIL.get(int(user_id))
    if until is None:
        return False
    if time.monotonic() > until:
        _PHOTO_FLOW_UNTIL.pop(int(user_id), None)
        return False
    return True


def _is_table_chart_callback(event: TelegramObject) -> bool:
    if not isinstance(event, CallbackQuery):
        return False
    data = event.data or ""
    return data.startswith(msg.CB_TABLE_CHART_PREFIX) or data.startswith(msg.CB_WB_CHART_PREFIX)


def _is_document_message(event: TelegramObject) -> bool:
    """Таблицы и документы не режем cooldown — иначе xlsx «пропадает» без ответа."""
    return isinstance(event, Message) and bool(getattr(event, "document", None))


def _is_image_menu_reply_button(event: TelegramObject) -> bool:
    if not isinstance(event, Message):
        return False
    from platforms.telegram_utils import is_image_reply_button_text

    return is_image_reply_button_text(getattr(event, "text", None))


def _is_reply_nav_button_message(event: TelegramObject) -> bool:
    """Reply-кнопки главного/создать-меню — не режем cooldown."""
    if not isinstance(event, Message):
        return False
    from platforms.telegram_utils import is_reply_nav_button_text

    return is_reply_nav_button_text(getattr(event, "text", None))


def _is_blogger_callback(event: TelegramObject) -> bool:
    if not isinstance(event, CallbackQuery):
        return False
    data = (event.data or "").strip()
    return data.startswith(
        (
            msg.CB_BLOG_ADAPT_PREFIX,
            msg.CB_BLOG_BACK_PREFIX,
            msg.CB_BLOG_HASH_PREFIX,
            msg.CB_BLOG_RUN_ADAPT_PREFIX,
            msg.CB_BLOGGER_COVER_PREFIX,
            msg.CB_BLOG_ART_PREFIX,
            msg.CB_BLOGGER_COVER_UPLOAD_FACE_PREFIX,
            msg.CB_BLOGGER_COVER_NO_FACE_PREFIX,
            msg.CB_COVER_GENERATE_PREFIX,
            msg.CB_ADAPT_TARGET_PREFIX,
        )
    )


def _is_whitelisted_callback(event: TelegramObject) -> bool:
    if _is_table_chart_callback(event):
        return True
    if _is_blogger_callback(event):
        return True
    if not isinstance(event, CallbackQuery):
        return False
    data = (event.data or "").strip()
    if data in WHITELISTED_CALLBACK_DATA:
        return True
    if data == msg.CB_CREATE_IMAGE or data.startswith(msg.CB_IMG_PREFIX):
        return True
    if data in (
        msg.CB_CREATE_TEXT,
        msg.CB_CREATE_ANIMATE,
        msg.CB_CREATE_VIDEO,
        msg.CB_CREATE_MUSIC,
        msg.CB_BACK_CREATE,
        msg.CB_BACK_TO_TOOLS,
        msg.CB_HD_SECTION,
    ):
        return True
    if data.startswith(msg.CB_SET_ROLE_PREFIX) or data.startswith(msg.CB_TEXT_ROLE_PREFIX):
        return True
    if data.startswith(msg.CB_CHAT_HINT_PREFIX) or data.startswith(msg.CB_STD_REPLY_PREFIX):
        return True
    if data in (
        msg.CB_SHOW_LIFESTYLE_SUBCATEGORIES,
        msg.CB_BACK_TO_ROLES_MENU,
        msg.CB_SHOW_TABLE_SUBCATEGORIES,
    ):
        return True
    if data.startswith(msg.CB_AUDIT_PLATFORM_PREFIX):
        return True
    for prefix in (
        msg.CB_REVIEW_APPROVE_PREFIX,
        msg.CB_REVIEW_REJECT_PREFIX,
        msg.CB_GALLERY_APPROVE_PREFIX,
        msg.CB_GALLERY_REJECT_PREFIX,
    ):
        if data.startswith(prefix):
            return True
    return False


class ThrottlingMiddleware(BaseMiddleware):
    """aiogram 3 middleware: 1 событие в ``cooldown`` секунд на user_id."""

    def __init__(self, cooldown: float = DEFAULT_COOLDOWN_SEC) -> None:
        self.cooldown = float(cooldown)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)

        if _is_whitelisted_callback(event):
            return await handler(event, data)

        user_id = _user_id_of(event)
        if user_id is not None and isinstance(event, Message):
            text = (getattr(event, "text", None) or "").strip()
            if text:
                from platforms.image_menu_flow import message_looks_like_photo_prompt

                if message_looks_like_photo_prompt(text):
                    mark_photo_flow(user_id)
                    return await handler(event, data)

        if user_id is not None and isinstance(event, Message) and is_photo_flow_active(user_id):
            return await handler(event, data)

        if _is_image_menu_reply_button(event):
            return await handler(event, data)

        if _is_reply_nav_button_message(event):
            return await handler(event, data)

        if _is_document_message(event):
            return await handler(event, data)

        if user_id is None:
            return await handler(event, data)

        now = time.monotonic()
        last = _LAST_CALL_AT.get(user_id, 0.0)
        if now - last < self.cooldown:
            kind = "callback" if isinstance(event, CallbackQuery) else "message"
            metrics.incr("throttle.blocked", {"kind": kind})
            logger.info(
                "throttle: blocked user_id=%s gap=%.3fs cooldown=%.1fs",
                user_id,
                now - last,
                self.cooldown,
            )
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer(DEFAULT_ALERT_TEXT, show_alert=False)
                except Exception:
                    logger.debug("throttle: callback.answer failed", exc_info=True)
            elif isinstance(event, Message):
                try:
                    await event.answer(DEFAULT_ALERT_TEXT, parse_mode="HTML")
                except Exception:
                    logger.debug("throttle: message.answer failed", exc_info=True)
            return None

        _LAST_CALL_AT[user_id] = now
        return await handler(event, data)


def reset_throttle(user_id: int) -> None:
    """Сбросить cooldown конкретного юзера (тесты / photo-flow entry)."""

    _LAST_CALL_AT.pop(int(user_id), None)


__all__ = (
    "DEFAULT_COOLDOWN_SEC",
    "DEFAULT_ALERT_TEXT",
    "PHOTO_FLOW_BYPASS_SEC",
    "WHITELISTED_CALLBACK_DATA",
    "ThrottlingMiddleware",
    "clear_photo_flow",
    "is_photo_flow_active",
    "mark_photo_flow",
    "reset_throttle",
)
