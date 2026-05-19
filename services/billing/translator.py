"""Скрытый перевод русских промптов на английский (OpenRouter)."""

from __future__ import annotations

import logging

from config import Settings
from services.ai_text import ask_ai_messages
from services.billing.pricing import FREE_CHAT_MODEL

logger = logging.getLogger(__name__)


async def translate_prompt_to_english(settings: Settings, russian_text: str) -> str:
    """Перевод для Replicate; при ошибке возвращает исходник."""
    text = (russian_text or "").strip()
    if not text:
        return text
    if not settings.openrouter_key:
        return text
    prompt = (
        "Translate the following image/video generation prompt to professional English. "
        "Output ONLY the English prompt, no quotes or explanations.\n\n"
        f"{text}"
    )
    try:
        out = await ask_ai_messages(
            settings,
            [{"role": "user", "content": prompt}],
            timeout=settings.openrouter_timeout_sec,
            models=[FREE_CHAT_MODEL],
        )
        translated = (out or "").strip()
        return translated or text
    except Exception:
        logger.warning("translate_prompt failed, using original", exc_info=True)
        return text
