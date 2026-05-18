"""
Use-case: приём текстового промпта для генерации изображения после выбора модели.

Списание кристаллов для PRO-моделей, учёт дневного лимита Free, постановка фоновой задачи ``fire_photo_job``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from config import Settings
from services.generation_jobs import fire_photo_job
from services.repository import get_user_row, try_consume_crystals, try_consume_daily_photo_slot
from services.tariffs import TariffName, normalize_tariff, queue_priority_for_tariff

if TYPE_CHECKING:
    from aiogram import Bot


class PhotoGenOutcome(str, Enum):
    NEED_PROMPT = "need_prompt"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    DAILY_LIMIT_EXCEEDED = "daily_limit_exceeded"
    SUCCESS = "success"


@dataclass(frozen=True)
class PhotoGenResult:
    """Результат ``run_photo_generation_turn``."""

    outcome: PhotoGenOutcome
    vip_priority: bool = False


async def run_photo_generation_turn(
    settings: Settings,
    bot: "Bot",
    chat_id: int,
    user_id: int,
    image_model_id: str,
    image_model_label: str,
    prompt: str,
) -> PhotoGenResult:
    """
    Списывает ресурсы и ставит задачу генерации фото (заглушка/воркер).

    Вход:
        settings, bot, chat_id, user_id — контекст Telegram и конфиг.
        image_model_id / image_model_label — модель для тарификации и подпись для логов/сообщений.
        prompt — текст промпта (уже strip снаружи).

    Возвращает:
        ``PhotoGenResult``; при ``SUCCESS`` задача уже поставлена в asyncio.
    """
    if not prompt:
        return PhotoGenResult(outcome=PhotoGenOutcome.NEED_PROMPT)

    row = await get_user_row(user_id)
    tariff = normalize_tariff(row.tariff)
    priority = queue_priority_for_tariff(tariff)
    used_slot = False
    charged_crystals = 0
    if tariff is TariffName.FREE and image_model_id == settings.free_image_model:
        ok, _ = await try_consume_daily_photo_slot(user_id, settings.free_daily_photo_limit)
        if not ok:
            return PhotoGenResult(outcome=PhotoGenOutcome.DAILY_LIMIT_EXCEEDED)
        used_slot = True
    else:
        charged_crystals = settings.cost_image_pro
        if not await try_consume_crystals(user_id, charged_crystals):
            return PhotoGenResult(outcome=PhotoGenOutcome.INSUFFICIENT_BALANCE)

    fire_photo_job(
        bot,
        chat_id,
        user_id,
        image_model_label,
        prompt,
        used_slot,
        charged_crystals,
        priority=priority,
    )
    return PhotoGenResult(outcome=PhotoGenOutcome.SUCCESS, vip_priority=(tariff is TariffName.ULTRA))
