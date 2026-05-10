"""
Use-case: генерация текстового промпта для картинки (меню «Генерация промпта»).

Списание энергии как за текстовый запрос (``cost_text_pro``), вызов OpenRouter через ``ask_ai_text``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from config import Settings
from services.ai_text import ask_ai_text
from services.image_prompt import build_image_prompt_request
from services.repository import try_consume_energy, update_balance

logger = logging.getLogger(__name__)


class ImagePromptOutcome(str, Enum):
    NEED_TEXT = "need_text"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    AI_FAILED = "ai_failed"
    SUCCESS = "success"


@dataclass(frozen=True)
class ImagePromptResult:
    """Результат ``run_image_prompt_turn``."""

    outcome: ImagePromptOutcome
    assistant_text: str | None = None


async def run_image_prompt_turn(settings: Settings, user_id: int, user_text: str) -> ImagePromptResult:
    """
    Один полный цикл «пользователь ввёл описание сцены → промпт EN/RU».

    Вход:
        settings — конфиг.
        user_id — Telegram user id.
        user_text — нормализованный текст (без strip внутри, вызывающий уже привёл к strip или пусто).

    Возвращает:
        ``ImagePromptResult`` с исходом и текстом при ``SUCCESS``.
    """
    if not user_text:
        return ImagePromptResult(outcome=ImagePromptOutcome.NEED_TEXT)

    if not await try_consume_energy(user_id, settings.cost_text_pro):
        return ImagePromptResult(outcome=ImagePromptOutcome.INSUFFICIENT_BALANCE)

    payload = build_image_prompt_request(user_text)
    try:
        answer = await ask_ai_text(settings, payload)
    except Exception:
        logger.exception("run_image_prompt_turn: AI failed user_id=%s", user_id)
        await update_balance(user_id, "energy", settings.cost_text_pro)
        return ImagePromptResult(outcome=ImagePromptOutcome.AI_FAILED)

    return ImagePromptResult(outcome=ImagePromptOutcome.SUCCESS, assistant_text=answer)
