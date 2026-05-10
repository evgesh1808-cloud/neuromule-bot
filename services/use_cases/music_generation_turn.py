"""Use-case: описание трека — списание энергии и постановка ``fire_music_job``."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from config import Settings
from services.generation_jobs import fire_music_job
from services.repository import get_user_row, try_consume_energy
from services.tariffs import can_use_music, normalize_tariff

if TYPE_CHECKING:
    from aiogram import Bot


class MusicGenOutcome(str, Enum):
    NEED_HINT = "need_hint"
    FORBIDDEN_BY_TARIFF = "forbidden_by_tariff"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    SUCCESS = "success"


@dataclass(frozen=True)
class MusicGenResult:
    outcome: MusicGenOutcome
    upgrade_to: str | None = None


async def run_music_generation_turn(
    settings: Settings,
    bot: "Bot",
    chat_id: int,
    user_id: int,
    style_hint: str,
) -> MusicGenResult:
    """
    Вход: settings, bot, chat_id, user_id, style_hint (уже strip снаружи).
    Возвращает: ``MusicGenResult``; при SUCCESS задача поставлена.
    """
    if not style_hint:
        return MusicGenResult(outcome=MusicGenOutcome.NEED_HINT)
    row = await get_user_row(user_id)
    tariff = normalize_tariff(row.tariff)
    if not can_use_music(tariff):
        return MusicGenResult(outcome=MusicGenOutcome.FORBIDDEN_BY_TARIFF, upgrade_to="smart")
    if not await try_consume_energy(user_id, settings.cost_music):
        return MusicGenResult(outcome=MusicGenOutcome.INSUFFICIENT_BALANCE)
    fire_music_job(bot, chat_id, user_id, style_hint[:500])
    return MusicGenResult(outcome=MusicGenOutcome.SUCCESS)
