"""Подготовка промпта обложки блогера для Imagen 4 (конкретные объекты, без абстракций)."""

from __future__ import annotations

import logging
import re

from config import Settings
from services.ai_text import ask_ai_messages
from services.billing.pricing import FREE_CHAT_MODEL

logger = logging.getLogger(__name__)

_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")

# Абстрактные метафоры, которые Imagen плохо интерпретирует.
_ABSTRACT_CLAUSE_RE = re.compile(
    r",?\s*(?:"
    r"representing|symbolizing|symbolising|signifying|illustrating|"
    r"embodying|expressing|conveying|evoking|depicting the concept of|"
    r"showing the concept of|concept of|as a symbol of|as a metaphor for|"
    r"visual metaphor(?:\s+of|\s+for)?|metaphor(?:\s+of|\s+for)?|"
    r"human happiness|crypto success|feelings of|feeling of|"
    r"символизирующ\w*|отражающ\w*|воплощающ\w*|метафора\s+сути|"
    r"как символ\w*|концепци\w*|чувств\w*"
    r")\s+[^,;.\n]+",
    re.IGNORECASE,
)

_BANNED_ABSTRACT_TERMS_RE = re.compile(
    r"\b(?:"
    r"success|future|happiness|hope|inspiration|ambition|prosperity|"
    r"feelings?|emotions?|dreams?|vision|liberty|freedom|"
    r"успех\w*|будущ\w*|счаст\w*|надежд\w*|вдохновен\w*|эмоци\w*"
    r")\b",
    re.IGNORECASE,
)

_OPTIMIZER_PREAMBLE_RE = re.compile(
    r"^(?:"
    r"here(?:'s| is) (?:your )?prompt|output:|final prompt:|"
    r"вот (?:ваш )?промпт|готово[!,.]?|ответ:"
    r")\s*:?\s*",
    re.IGNORECASE,
)

IMAGEN4_OPTIMIZER_SYSTEM_PROMPT = """You are a technical prompt optimizer for Imagen 4 image generation.
Your task is to translate the input to English (if it is in Russian) and completely remove abstract concepts, metaphors, and intangible ideas.

FORBIDDEN: Use words like "symbolizing", "representing", "concept of", "feelings", "success", "future", and any abstract metaphors (for example, "a chart soaring into the sky as a symbol of hope").
ALLOWED: Write only physical, tangible objects, clothing, people, light, camera angle, location, and style.
OUTPUT FORMAT: Output strictly the final English prompt on a single line. No intro text, no "Here is your prompt:".

Example:
Input: "A professional photo representing crypto success and human happiness"
Output: "A professional cinematic photo of a smiling man in a modern office looking at a laptop, neon soft lighting, depth of field, 8k resolution"
"""


def _has_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC_RE.search(text or ""))


def _strip_optimizer_preamble(text: str) -> str:
    result = (text or "").strip()
    while True:
        cleaned = _OPTIMIZER_PREAMBLE_RE.sub("", result, count=1).strip()
        if cleaned == result:
            break
        result = cleaned
    result = result.strip().strip('"').strip("'").strip()
    return re.sub(r"\s+", " ", result).strip()


def sanitize_blogger_image_prompt_for_imagen(raw: str) -> str:
    """Синхронная очистка: убирает абстракции, нормализует шаблон Imagen."""
    prompt = (raw or "").strip()
    if not prompt:
        return prompt

    prompt = _ABSTRACT_CLAUSE_RE.sub("", prompt)
    prompt = _BANNED_ABSTRACT_TERMS_RE.sub("", prompt)
    prompt = re.sub(r"\{[^}]+\}", "", prompt)
    prompt = re.sub(r"\s{2,}", " ", prompt).strip(" ,;")

    match = re.search(
        r"(?:professional cinematic photo of\s+)?(.+?)(?:,\s*4k|--ar\s*\d+:\d+|$)",
        prompt,
        flags=re.IGNORECASE | re.DOTALL,
    )
    subject = (match.group(1).strip() if match else prompt).strip(" ,;")
    subject = _ABSTRACT_CLAUSE_RE.sub("", subject).strip(" ,;")
    subject = _BANNED_ABSTRACT_TERMS_RE.sub("", subject).strip(" ,;")
    subject = re.sub(r"\s{2,}", " ", subject).strip(" ,;")
    if not subject:
        subject = "a clear photorealistic scene with concrete objects and natural lighting"

    return (
        f"A professional cinematic photo of {subject}, "
        "4k, photorealistic, commercial lighting --ar 16:9"
    )


async def optimize_image_prompt_for_imagen(settings: Settings, raw: str) -> str:
    """LLM-оптимизатор Imagen 4: перевод RU→EN + замена абстракций на физические объекты."""
    cleaned = sanitize_blogger_image_prompt_for_imagen(raw)
    if not settings.openrouter_key:
        return cleaned

    user_block = f"Input:\n{cleaned}"
    try:
        out = await ask_ai_messages(
            settings,
            [
                {"role": "system", "content": IMAGEN4_OPTIMIZER_SYSTEM_PROMPT},
                {"role": "user", "content": user_block},
            ],
            timeout=settings.openrouter_timeout_sec,
            models=[FREE_CHAT_MODEL],
            temperature=0.2,
        )
        optimized = _strip_optimizer_preamble(out.get("content") or "")
        if not optimized:
            return cleaned
        return sanitize_blogger_image_prompt_for_imagen(optimized)
    except Exception:
        logger.warning("optimize_image_prompt_for_imagen failed, using sanitized fallback", exc_info=True)
        return cleaned


async def prepare_blogger_cover_prompt(settings: Settings, raw: str) -> str:
    """Финальный английский промпт Flux Schnell для обложки блогера (без LLM)."""
    from services.blogger_cover import prepare_blogger_flux_prompt

    _ = settings
    return prepare_blogger_flux_prompt(raw)
