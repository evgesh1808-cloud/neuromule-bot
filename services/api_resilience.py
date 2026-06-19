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


class ExternalApiError(Exception):
    """Ошибка провайдера (OpenRouter, Replicate, Gemini, Suno) после списания ресурсов."""

    def __init__(self, provider: str, message: str = "") -> None:
        self.provider = provider
        super().__init__(message or provider)


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

    try:
        await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
    except Exception:
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            logger.debug("notify_user_safe failed chat_id=%s", chat_id, exc_info=True)


async def fail_generation_task(
    task: GenTask,
    *,
    user_message: str,
    log_msg: str = "",
) -> None:
    """Пометить задачу failed, вернуть ресурсы, уведомить пользователя."""
    task.status = "failed"
    if log_msg:
        logger.error("%s task_id=%s user_id=%s", log_msg, task.task_id, task.user_id)
    await refund_generation_task(task)
    await notify_user_safe(task.bot, task.chat_id, user_message)


def wrap_http_error(provider: str, exc: BaseException) -> ExternalApiError:
    if isinstance(exc, httpx.TimeoutException):
        return ExternalApiError(provider, f"{provider}: timeout")
    if isinstance(exc, httpx.HTTPStatusError):
        return ExternalApiError(provider, f"{provider}: HTTP {exc.response.status_code}")
    return ExternalApiError(provider, str(exc))
