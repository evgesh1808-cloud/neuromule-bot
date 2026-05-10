"""Use-case: текстовый промпт для видео — списание энергии и постановка ``fire_video_job``."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from config import Settings
from services.generation_jobs import fire_video_job
from services.repository import get_user_row, try_consume_energy
from services.tariffs import TariffName, can_use_video, normalize_tariff, queue_priority_for_tariff

if TYPE_CHECKING:
    from aiogram import Bot


class VideoGenOutcome(str, Enum):
    NEED_PROMPT = "need_prompt"
    FORBIDDEN_BY_TARIFF = "forbidden_by_tariff"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    SUCCESS = "success"


@dataclass(frozen=True)
class VideoGenResult:
    outcome: VideoGenOutcome
    upgrade_to: str | None = None
    vip_priority: bool = False


async def run_video_generation_turn(
    settings: Settings,
    bot: "Bot",
    chat_id: int,
    user_id: int,
    prompt: str,
) -> VideoGenResult:
    """
    Вход: settings, bot, chat_id, user_id, prompt (уже strip).
    Возвращает: ``VideoGenResult``; при SUCCESS задача поставлена.
    """
    if not prompt:
        return VideoGenResult(outcome=VideoGenOutcome.NEED_PROMPT)
    row = await get_user_row(user_id)
    tariff = normalize_tariff(row.tariff)
    if not can_use_video(tariff):
        return VideoGenResult(
            outcome=VideoGenOutcome.FORBIDDEN_BY_TARIFF,
            upgrade_to="smart" if tariff in (TariffName.FREE, TariffName.MINI) else "ultra",
        )
    if not await try_consume_energy(user_id, settings.cost_video):
        return VideoGenResult(outcome=VideoGenOutcome.INSUFFICIENT_BALANCE)
    fire_video_job(bot, chat_id, user_id, priority=queue_priority_for_tariff(tariff))
    return VideoGenResult(outcome=VideoGenOutcome.SUCCESS, vip_priority=(tariff is TariffName.ULTRA))
