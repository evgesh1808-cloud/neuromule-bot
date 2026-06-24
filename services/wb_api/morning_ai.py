"""Утренний ИИ-инсайт — минимальный расход токенов OpenRouter."""

from __future__ import annotations

import logging

from config import Settings
from services.ai_text import ask_ai_messages

logger = logging.getLogger(__name__)

_MORNING_INSIGHT_SYSTEM = (
    "Сделай из этих сухих данных утренний мотивирующий бизнес-инсайт для селлера. "
    "2–3 коротких предложения. Только Telegram HTML: <b>, <i>. Без Markdown и приветствий."
)
_MAX_TOKENS = 120
_TEMPERATURE = 0.35


async def generate_morning_insight(
    settings: Settings,
    digest_line: str,
    *,
    http_client: object | None = None,
) -> str:
    """
    Фишка #3: один короткий user-текст → минимальный ответ модели.

    При сбое возвращает пустую строку (воркер использует только локальные метрики).
    """
    line = (digest_line or "").strip()
    if not line:
        return ""
    try:
        completion = await ask_ai_messages(
            settings,
            [
                {"role": "system", "content": _MORNING_INSIGHT_SYSTEM},
                {"role": "user", "content": line},
            ],
            http_client=http_client,
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
        )
    except Exception:
        logger.exception("wb morning insight AI failed")
        return ""
    return (completion.get("content") or "").strip()
