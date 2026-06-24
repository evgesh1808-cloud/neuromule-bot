"""Скрытый перевод русских промптов на английский (OpenRouter) + Prompt Enhancer.

Prompt Enhancer — это ИИ-режиссёр: после перевода добавляет кинематографичные
ключевые слова (cinematic lighting, hyper-realistic, 8k, photorealistic) и просит
LLM реструктурировать промпт под Replicate / Flux PRO / Luma. При любой ошибке
возвращает исходный (или хотя бы переведённый) текст — пайплайн генерации не
ломается из-за сбоев OpenRouter.
"""

from __future__ import annotations

import logging

from config import Settings
from services.ai_text import ask_ai_messages
from services.billing.pricing import FREE_CHAT_MODEL

logger = logging.getLogger(__name__)


CINEMATIC_KEYWORDS = "cinematic lighting, hyper-realistic, 8k, photorealistic"

SUNO_HIFI_KEYWORDS = "cinematic mix, high fidelity, tight production"


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
        translated = (out.get("content") or "").strip()
        return translated or text
    except Exception:
        logger.warning("translate_prompt failed, using original", exc_info=True)
        return text


def _looks_already_enhanced(text: str) -> bool:
    low = text.lower()
    return all(kw in low for kw in ("cinematic", "8k", "photorealistic"))


async def enhance_video_prompt_for_replicate(
    settings: Settings, russian_or_english_text: str
) -> str:
    """ИИ-режиссёр для Replicate/Flux PRO.

    Шаги:
        1. Переводит русский текст на английский (если требуется).
        2. Просит LLM переписать промпт как кинематографичный (camera angles,
           lighting, mood) с обязательными ключами ``CINEMATIC_KEYWORDS``.
        3. Если LLM недоступен или вернул мусор — добавляет ``CINEMATIC_KEYWORDS``
           к переведённому тексту как fallback.
    """
    src = (russian_or_english_text or "").strip()
    if not src:
        return src
    if _looks_already_enhanced(src):
        return src

    translated = await translate_prompt_to_english(settings, src)

    if not settings.openrouter_key:
        return _append_cinematic(translated)

    director_prompt = (
        "You are a world-class cinematic AI director writing prompts for Replicate "
        "video / Flux PRO image models.\n"
        "Rewrite the user's idea below into a single, vivid English prompt of up to "
        "60 words. Add concrete camera framing (close-up, wide shot…), lighting "
        "(soft rim, golden hour, neon…), mood, and texture detail. Always include "
        f"the keywords: {CINEMATIC_KEYWORDS}.\n"
        "Output ONLY the final English prompt, no labels, no quotes, no explanations.\n\n"
        f"USER IDEA: {translated}"
    )
    try:
        out = await ask_ai_messages(
            settings,
            [{"role": "user", "content": director_prompt}],
            timeout=settings.openrouter_timeout_sec,
            models=[FREE_CHAT_MODEL],
        )
        enhanced = (out.get("content") or "").strip().strip('"').strip("'")
        if not enhanced or len(enhanced) < len(translated) // 2:
            return _append_cinematic(translated)
        if not _looks_already_enhanced(enhanced):
            enhanced = _append_cinematic(enhanced)
        return enhanced
    except Exception:
        logger.warning("enhance_video_prompt failed, using fallback", exc_info=True)
        return _append_cinematic(translated)


def _append_cinematic(text: str) -> str:
    base = text.strip().rstrip(".,;:")
    if _looks_already_enhanced(base):
        return base
    return f"{base}, {CINEMATIC_KEYWORDS}"


def _looks_already_hifi(text: str) -> bool:
    low = text.lower()
    return all(kw in low for kw in ("cinematic mix", "high fidelity", "tight production"))


def _append_hifi(text: str) -> str:
    base = text.strip().rstrip(".,;:")
    if _looks_already_hifi(base):
        return base
    return f"{base}, {SUNO_HIFI_KEYWORDS}"


async def enhance_music_style_prompt(
    settings: Settings, russian_or_english_text: str
) -> str:
    """Prompt Enhancer для Suno AI: переводит стиль на EN и шьёт hi-fi теги.

    Поведение симметрично :func:`enhance_video_prompt_for_replicate`:
        1. Переводит русский ввод стиля на профессиональный английский.
        2. Просит LLM переписать в плотный музыкальный промпт до 40 слов
           (жанр, темп, инструменты, вокал, референс) и обязательно вшить
           ``SUNO_HIFI_KEYWORDS``.
        3. При любой ошибке/недоступности OpenRouter — возвращает перевод
           с дополненными hi-fi-ключами, чтобы Suno получил релевантный стиль.
    """

    src = (russian_or_english_text or "").strip()
    if not src:
        return src
    if _looks_already_hifi(src):
        return src

    translated = await translate_prompt_to_english(settings, src)

    if not settings.openrouter_key:
        return _append_hifi(translated)

    director_prompt = (
        "You are a world-class music producer writing English style prompts for "
        "Suno AI v4. Rewrite the user's idea below into a single, vivid English "
        "style prompt of up to 40 words. Use concrete music terms: genre, BPM, "
        "key instruments, vocal type, mood, reference artists. Always include "
        f"the keywords: {SUNO_HIFI_KEYWORDS}.\n"
        "Output ONLY the final English style prompt, no labels, no quotes.\n\n"
        f"USER IDEA: {translated}"
    )
    try:
        out = await ask_ai_messages(
            settings,
            [{"role": "user", "content": director_prompt}],
            timeout=settings.openrouter_timeout_sec,
            models=[FREE_CHAT_MODEL],
        )
        enhanced = (out.get("content") or "").strip().strip('"').strip("'")
        if not enhanced or len(enhanced) < len(translated) // 2:
            return _append_hifi(translated)
        if not _looks_already_hifi(enhanced):
            enhanced = _append_hifi(enhanced)
        return enhanced
    except Exception:
        logger.warning("enhance_music_style failed, using fallback", exc_info=True)
        return _append_hifi(translated)
