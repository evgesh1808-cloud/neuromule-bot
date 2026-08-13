"""Отказоустойчивость внешних API и компенсация списаний биллинга."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from services.billing.store import refund_charge
from services.repository import rollback_daily_photo_slot, update_balance

if TYPE_CHECKING:
    from aiogram import Bot
    from services.generation_jobs import GenTask

logger = logging.getLogger(__name__)

# Telegram Bot API: лимит текста сообщения 4096; держим запас + не светим stack/body.
_TG_SAFE_ERR_CHARS = 200
_TG_MAX_MESSAGE_CHARS = 3900


def clip_error_text(exc: object, *, limit: int = _TG_SAFE_ERR_CHARS) -> str:
    """Короткий фрагмент ошибки для UI / безопасных логов (без тела HTTP/base64)."""
    raw = str(exc or "").replace("\x00", " ").strip() or "unknown"
    if len(raw) <= limit:
        return raw
    return raw[: max(1, limit - 3)].rstrip() + "..."


def clip_telegram_text(text: str, *, limit: int = _TG_MAX_MESSAGE_CHARS) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: max(1, limit - 3)].rstrip() + "..."


class ExternalApiError(Exception):
    """Ошибка провайдера (OpenRouter, Replicate, Gemini, Suno) после списания ресурсов."""

    def __init__(self, provider: str, message: str = "") -> None:
        self.provider = provider
        # Сразу режем: иначе last_err/exc раздувают Telegram sendMessage.
        safe = clip_error_text(message or provider)
        super().__init__(safe)


async def refund_generation_task(task: GenTask) -> None:
    """Вернуть ⚡/💎 по ``billing_charge_id`` или legacy-полям задачи."""
    if task.billing_charge_id:
        try:
            await refund_charge(task.billing_charge_id)
            return
        except Exception:
            logger.exception("refund_charge failed charge_id=%s", task.billing_charge_id)
    if task.charged_crystals:
        try:
            await update_balance(task.user_id, "crystals", task.charged_crystals)
        except Exception:
            logger.exception("legacy crystal refund failed user_id=%s", task.user_id)
    if task.used_daily_slot:
        try:
            await rollback_daily_photo_slot(task.user_id)
        except Exception:
            logger.exception("photo slot rollback failed user_id=%s", task.user_id)


async def notify_user_safe(bot: Bot, chat_id: int, text: str) -> None:
    from aiogram.enums import ParseMode

    safe = clip_telegram_text(text)
    try:
        await bot.send_message(chat_id, safe, parse_mode=ParseMode.HTML)
    except Exception:
        try:
            # Без HTML и ещё короче — защита от «message is too long» / parse entities.
            await bot.send_message(chat_id, clip_telegram_text(safe, limit=3500))
        except Exception:
            logger.debug("notify_user_safe failed chat_id=%s", chat_id, exc_info=True)


def _reset_failed_task(task: GenTask) -> None:
    """Сброс полей задачи после fail — без повтора упавшего payload при ретраях."""
    task.status = "failed"
    task.prompt = None
    task.file_id = None
    task.music_lyrics = None
    task.music_continue_clip_id = None


async def _edit_status_message(task: GenTask, text: str) -> bool:
    """Заменить status_msg («Мул ушёл…») на текст ошибки. True если edit успешен."""
    if getattr(task, "platform", "telegram") == "vk" or task.bot is None:
        return False
    msg_id = getattr(task, "status_message_id", None)
    if msg_id is None:
        return False
    from aiogram.enums import ParseMode
    from aiogram.exceptions import (
        TelegramBadRequest,
        TelegramForbiddenError,
        TelegramNetworkError,
        TelegramRetryAfter,
    )

    safe = clip_telegram_text(text)
    try:
        await task.bot.edit_message_text(
            chat_id=task.chat_id,
            message_id=int(msg_id),
            text=safe,
            parse_mode=ParseMode.HTML,
        )
        return True
    except TelegramRetryAfter as exc:
        logger.warning(
            "status edit retry_after=%s task=%s",
            getattr(exc, "retry_after", "?"),
            task.task_id,
        )
    except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError) as exc:
        logger.warning("status edit failed task=%s: %s", task.task_id, exc)
    except Exception:
        logger.error("status edit unexpected task=%s", task.task_id, exc_info=True)
    return False


async def fail_generation_task(
    task: GenTask,
    *,
    user_message: str,
    log_msg: str = "",
    exc: BaseException | None = None,
) -> None:
    """Пометить задачу failed, вернуть ресурсы, уведомить пользователя (короткий текст)."""
    status_message_id = getattr(task, "status_message_id", None)
    _reset_failed_task(task)
    # Не теряем id статуса: нужен для edit после reset.
    task.status_message_id = status_message_id
    if exc is not None:
        logger.error("Полная ошибка: %s", exc, exc_info=True)
    if log_msg:
        logger.error(
            "%s task_id=%s user_id=%s detail=%s",
            clip_error_text(log_msg, limit=500),
            task.task_id,
            task.user_id,
            clip_error_text(exc) if exc is not None else "",
        )
    await refund_generation_task(task)
    cleanup_ids = getattr(task, "cleanup_message_ids", ()) or ()
    if task.bot is not None and cleanup_ids:
        for raw_id in cleanup_ids:
            if raw_id is None:
                continue
            try:
                await task.bot.delete_message(
                    chat_id=task.chat_id,
                    message_id=int(raw_id),
                )
            except Exception:
                logger.debug(
                    "fail_generation_task cleanup delete skipped task=%s msg=%s",
                    task.task_id,
                    raw_id,
                    exc_info=True,
                )
        task.cleanup_message_ids = ()
    # Предпочитаем edit «мула»; иначе — новое сообщение.
    if await _edit_status_message(task, user_message):
        return
    platform = getattr(task, "platform", "telegram")
    if platform == "vk":
        from platforms.vk_runtime import notify_vk_user

        await notify_vk_user(task.chat_id, user_message)
        return
    if task.bot is not None:
        await notify_user_safe(task.bot, task.chat_id, user_message)


def wrap_http_error(provider: str, exc: BaseException) -> ExternalApiError:
    if isinstance(exc, httpx.TimeoutException):
        return ExternalApiError(provider, f"{provider}: timeout")
    if isinstance(exc, httpx.HTTPStatusError):
        return ExternalApiError(provider, f"{provider}: HTTP {exc.response.status_code}")
    return ExternalApiError(provider, clip_error_text(exc))
