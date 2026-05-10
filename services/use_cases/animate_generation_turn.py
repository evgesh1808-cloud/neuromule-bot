"""Use-case: оживление фото — списание энергии и постановка ``fire_animate_job``."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from config import Settings
from services.generation_jobs import fire_animate_job
from services.repository import get_user_row, try_consume_energy
from services.tariffs import can_use_animate, normalize_tariff

if TYPE_CHECKING:
    from aiogram import Bot


class AnimateGenOutcome(str, Enum):
    FORBIDDEN_BY_TARIFF = "forbidden_by_tariff"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    SUCCESS = "success"


@dataclass(frozen=True)
class AnimateGenResult:
    outcome: AnimateGenOutcome
    upgrade_to: str | None = None


async def run_animate_generation_turn(
    settings: Settings, bot: "Bot", chat_id: int, user_id: int
) -> AnimateGenResult:
    """
    Вход: settings, bot, chat_id, user_id (фото уже принято хендлером).
    Возвращает: ``AnimateGenResult``; при SUCCESS задача поставлена.
    """
    row = await get_user_row(user_id)
    tariff = normalize_tariff(row.tariff)
    if not can_use_animate(tariff):
        return AnimateGenResult(outcome=AnimateGenOutcome.FORBIDDEN_BY_TARIFF, upgrade_to="ultra")
    if not await try_consume_energy(user_id, settings.cost_animate):
        return AnimateGenResult(outcome=AnimateGenOutcome.INSUFFICIENT_BALANCE)
    fire_animate_job(bot, chat_id, user_id)
    return AnimateGenResult(outcome=AnimateGenOutcome.SUCCESS)
