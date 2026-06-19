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

Тонкости:
* Пул `_LAST_CALL_AT` живёт в памяти процесса. После рестарта чист —
  это приемлемо, потому что cooldown короткий (2 секунды по умолчанию).
* Список «whitelisted» callback-данных (без троттлинга) выводит из-под
  фильтра низкорисковые действия: открытие меню, отказ от шеринга,
  принятие TOS — там нет финансовых операций, юзера нельзя «спамить
  спугнуть».
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
DEFAULT_ALERT_TEXT: Final = (
    "⏳ Не спеши! NeuroMule обрабатывает твой прошлый запрос."
)

# Дешёвые / safe-операции — не троттлим, чтобы не раздражать UX.
# ``CB_CHECK_SUBSCRIPTION`` — кнопка «✅ Принять условия и Запустить»
# в новой TOS-заслонке (start_onboarding); пропускаем без cooldown,
# чтобы flow «принятие оферты ⇒ главное меню» был мгновенным.
WHITELISTED_CALLBACK_DATA: Final[frozenset[str]] = frozenset(
    {
        msg.CB_ACCEPT_LEGAL_TOS,
        msg.CB_CHECK_SUBSCRIPTION,
        msg.CB_RECHECK_SUBSCRIPTION,
        msg.CB_REFRESH_PROFILE,
        msg.CB_GALLERY_CANCEL,
        msg.CB_GALLERY_CONFIRM,
        msg.CB_SHARE_TO_GALLERY,
    }
)

# Per-user последний tick (UNIX seconds).
_LAST_CALL_AT: dict[int, float] = {}


def _user_id_of(event: TelegramObject) -> int | None:
    user = getattr(event, "from_user", None)
    if user is None:
        return None
    return int(getattr(user, "id", 0)) or None


def _is_table_chart_callback(event: TelegramObject) -> bool:
    if not isinstance(event, CallbackQuery):
        return False
    return (event.data or "").startswith(msg.CB_TABLE_CHART_PREFIX)


def _is_whitelisted_callback(event: TelegramObject) -> bool:
    if _is_table_chart_callback(event):
        return True
    if not isinstance(event, CallbackQuery):
        return False
    data = (event.data or "").strip()
    if data in WHITELISTED_CALLBACK_DATA:
        return True
    # Префиксы админ-модерации / TOS-навигации тоже выводим из-под
    # троттлинга (это редкие события, не атакующая поверхность).
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
        # Пропускаем системные апдейты (PreCheckoutQuery, ChosenInlineResult и т.д.).
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)

        if _is_whitelisted_callback(event):
            return await handler(event, data)

        user_id = _user_id_of(event)
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
                # Гасим анимацию часиков и показываем мягкую плашку.
                try:
                    await event.answer(DEFAULT_ALERT_TEXT, show_alert=False)
                except Exception:
                    logger.debug("throttle: callback.answer failed", exc_info=True)
            # Для Message не отвечаем явно: спамящий FREE-юзер просто
            # не получит лишних эхо-сообщений (и так шумно в чате).
            return None

        _LAST_CALL_AT[user_id] = now
        return await handler(event, data)


def reset_throttle(user_id: int) -> None:
    """Тестовый/админский helper: сбросить cooldown конкретного юзера."""

    _LAST_CALL_AT.pop(int(user_id), None)


__all__ = (
    "DEFAULT_COOLDOWN_SEC",
    "DEFAULT_ALERT_TEXT",
    "WHITELISTED_CALLBACK_DATA",
    "ThrottlingMiddleware",
    "reset_throttle",
)
