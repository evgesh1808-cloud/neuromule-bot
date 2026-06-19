"""Безопасная отправка сервисных пушей пользователю (реф-бонус, премодерация и пр.).

Зачем отдельный модуль: «пользовательские» уведомления отличаются от
сообщений, инициированных самим пользователем, тем, что юзер мог:

* заблокировать бота (``TelegramForbiddenError``);
* удалить аккаунт (``TelegramBadRequest: chat not found / user is deactivated``);
* спамить кнопки → попасть под флуд-лимит Telegram (``TelegramRetryAfter``);
* быть недоступен по сети (``TelegramNetworkError``).

Во всех этих случаях падение одного юзера НЕ должно ронять реферальный
конвейер, очередь модерации или фоновый воркер. Поэтому здесь — единая
точка с **специализированными** ``aiogram.exceptions`` и
структурированным логированием с контекстом ``(user_id, reason)``.

Generic ``except Exception`` оставляем только для самой нижней ветки:
unknown error логируется как ERROR с ``exc_info=True``, конвейер
продолжает работать.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from services import metrics

logger = logging.getLogger(__name__)


async def safe_send_user_message(
    bot: Bot,
    user_id: int,
    text: str,
    *,
    context: str = "user_notify",
    parse_mode: str | None = None,
    disable_web_page_preview: bool | None = None,
    reply_markup: Any | None = None,
) -> bool:
    """Отправить сервисное сообщение пользователю.

    Возвращает ``True`` при успехе, ``False`` — если Telegram отказался
    доставлять (юзер заблокировал бота / удалил аккаунт / флуд-лимит / сеть).
    Любая иная ошибка логируется как ERROR, но НЕ пробрасывается —
    конвейер вызвавшего обработчика остаётся живым.

    ``context`` — короткое имя источника (например, ``"ref_bonus"``,
    ``"gallery_approve_notify"``), попадает во все лог-сообщения, чтобы
    в продакшене было видно, какой именно push'ер сломался."""

    try:
        await bot.send_message(
            user_id,
            text,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
            reply_markup=reply_markup,
        )
        metrics.incr("notify.sent", {"context": context})
        return True
    except TelegramForbiddenError:
        metrics.incr("notify.forbidden", {"context": context})
        logger.info(
            "telegram_notify: blocked by user context=%s user_id=%s",
            context,
            user_id,
        )
        return False
    except TelegramRetryAfter as exc:
        retry_after = getattr(exc, "retry_after", None)
        metrics.incr("notify.retry_after", {"context": context})
        logger.warning(
            "telegram_notify: flood limit context=%s user_id=%s retry_after=%s",
            context,
            user_id,
            retry_after,
        )
        return False
    except TelegramBadRequest as exc:
        metrics.incr("notify.bad_request", {"context": context})
        logger.warning(
            "telegram_notify: bad request context=%s user_id=%s reason=%s",
            context,
            user_id,
            str(exc)[:200],
        )
        return False
    except TelegramNetworkError as exc:
        metrics.incr("notify.network_error", {"context": context})
        logger.warning(
            "telegram_notify: network error context=%s user_id=%s reason=%s",
            context,
            user_id,
            str(exc)[:200],
        )
        return False
    except Exception:
        metrics.incr("notify.unexpected", {"context": context})
        logger.error(
            "telegram_notify: unexpected error context=%s user_id=%s",
            context,
            user_id,
            exc_info=True,
        )
        return False


__all__ = ("safe_send_user_message",)
