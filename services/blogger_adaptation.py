"""Точечные LLM-запросы для адаптации тела поста блогера под соцсети."""

from __future__ import annotations

import logging

from config import Settings
from services.ai_text import ask_ai_messages
from services.billing.pricing import PAID_CHAT_MODEL

logger = logging.getLogger(__name__)

_ADAPT_PLATFORMS = frozenset({"reels", "vc", "twitter"})

_PLATFORM_INSTRUCTIONS: dict[str, str] = {
    "reels": (
        "Ты — сценарист коротких видео. Возьми текст пользователя и перепиши его в сценарий для Reels/Shorts. "
        "Разбей текст на сцены, добавь таймкоды (00:00-00:03...) и краткие визуальные подсказки для кадра "
        "в квадратных скобках. Никакого лишнего текста от себя, пиши строго сценарий."
    ),
    "vc": (
        "Ты — b2b-редактор vc.ru. Разверни этот текст в экспертную статью. "
        "Добавь подзаголовки, углуби тезисы, сделай тон более аналитическим и серьезным. "
        "Обязательно сохрани структуру воздушных абзацев и HTML-теги жирного шрифта <b>тезис</b>."
    ),
    "twitter": (
        "Сожми этот текст до одного хлёсткого, хайпового твита (до 280 символов). "
        "Выжми максимум пользы или провокации, уложись в лимит, не используй эмодзи и лишнюю воду."
    ),
}

_PLATFORM_LABELS: dict[str, str] = {
    "reels": "Reels/Shorts",
    "vc": "VC.ru",
    "twitter": "Twitter/X",
}


def adapt_platform_label(platform: str) -> str:
    return _PLATFORM_LABELS.get(platform, platform.upper())


def is_valid_adapt_platform(platform: str) -> bool:
    return platform in _ADAPT_PLATFORMS


async def adapt_blogger_post_body(
    settings: Settings,
    *,
    source_body: str,
    platform: str,
) -> str | None:
    """Один дешёвый запрос: system-инструкция + только тело поста."""
    if not source_body.strip():
        return None
    if platform not in _ADAPT_PLATFORMS:
        return None

    instruction = _PLATFORM_INSTRUCTIONS[platform]
    messages = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": source_body},
    ]
    try:
        result = await ask_ai_messages(
            settings,
            messages,
            models=[PAID_CHAT_MODEL],
            max_tokens=1500,
            temperature=0.3,
        )
    except Exception:
        logger.exception("blogger adapt ask_ai_messages failed platform=%s", platform)
        return None

    content = (result.content or "").strip()
    return content or None
