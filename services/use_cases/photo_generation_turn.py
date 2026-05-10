"""
Use-case: приём текстового промпта для генерации изображения после выбора модели.

Списание энергии, учёт дневного лимита Free, постановка фоновой задачи ``fire_photo_job``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from config import Settings
from services.generation_jobs import fire_photo_job
from services.repository import get_user_row, try_consume_daily_photo_slot, try_consume_energy, update_balance
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
    image_model_label: str,
    prompt: str,
) -> PhotoGenResult:
    """
    Списывает ресурсы и ставит задачу генерации фото (заглушка/воркер).

    Вход:
        settings, bot, chat_id, user_id — контекст Telegram и конфиг.
        image_model_label — подпись модели для логов/сообщений.
        prompt — текст промпта (уже strip снаружи).

    Возвращает:
        ``PhotoGenResult``; при ``SUCCESS`` задача уже поставлена в asyncio.
    """
    if not prompt:
        return PhotoGenResult(outcome=PhotoGenOutcome.NEED_PROMPT)

    if not await try_consume_energy(user_id, settings.cost_image_pro):
        return PhotoGenResult(outcome=PhotoGenOutcome.INSUFFICIENT_BALANCE)

    row = await get_user_row(user_id)
    tariff = normalize_tariff(row.tariff)
    priority = queue_priority_for_tariff(tariff)
    used_slot = False
    if tariff is TariffName.FREE:
        ok, _ = await try_consume_daily_photo_slot(user_id, settings.free_daily_photo_limit)
        if not ok:
            await update_balance(user_id, "energy", settings.cost_image_pro)
            return PhotoGenResult(outcome=PhotoGenOutcome.DAILY_LIMIT_EXCEEDED)
        used_slot = True

    fire_photo_job(bot, chat_id, user_id, image_model_label, prompt, used_slot, priority=priority)
    return PhotoGenResult(outcome=PhotoGenOutcome.SUCCESS, vip_priority=(tariff is TariffName.ULTRA))
