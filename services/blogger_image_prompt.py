"""Подготовка промпта обложки блогера для Flux (premium lifestyle / editorial)."""

from __future__ import annotations

import logging
import re

from config import Settings
from services.ai_text import ask_ai_messages
from services.billing.pricing import FREE_CHAT_MODEL

logger = logging.getLogger(__name__)

_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")

# Абстрактные метафоры, которые генераторы плохо интерпретируют.
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

# Модель иногда эхом повторяет инструкцию вместо готового промпта.
_INSTRUCTION_ECHO_RE = re.compile(
    r"(?:"
    r"Generate a highly descriptive.*?formula precisely:\s*"
    r"|Follow this prompt formula precisely:\s*"
    r"|Output ONLY the clean.*?keywords\.?\s*"
    r"|1\.\s*Composition & Aesthetic:.*?(?=2\.|$)"
    r"|2\.\s*Subject Placement:.*?(?=3\.|$)"
    r"|3\.\s*Details & Textures:.*?(?=4\.|$)"
    r"|4\.\s*Lighting & Lens:.*?(?=Output|$)"
    r")",
    re.IGNORECASE | re.DOTALL,
)

_ASPECT_OR_TECH_SUFFIX_RE = re.compile(
    r"(?:,\s*)?(?:4k|8k|photorealistic|commercial lighting|\s*--ar\s*\d+:\d+)+\s*",
    re.IGNORECASE,
)

FLUX_OPTIMIZER_SYSTEM_PROMPT = """You are a technical prompt optimizer for Flux image generation (premium lifestyle / editorial blog covers).
Your task is to translate the input to English (if it is in Russian) and produce one clean, highly descriptive Flux prompt.

REQUIRED STYLE:
- high-end editorial lifestyle photography, magazine cover style, authentic aesthetic
- clear central subject/focal point suitable for later face or product reference integration
- realistic textures, natural/soft dramatic lighting, shallow depth of field, 35mm lens feel
FORBIDDEN: "3D render", "plastic texture", "cartoon", "generic illustration", aspect ratio flags (--ar), negative prompts, intro phrases.
OUTPUT: Only the final English prompt text. No markdown, no quotes wrapper, no "Here is your prompt".
"""

# Обратная совместимость импорта
IMAGEN4_OPTIMIZER_SYSTEM_PROMPT = FLUX_OPTIMIZER_SYSTEM_PROMPT


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
    """Очистка Flux-промпта обложки: без --ar/meta-инструкций, без жёсткого Imagen-шаблона."""
    prompt = (raw or "").strip()
    if not prompt:
        return prompt

    prompt = _INSTRUCTION_ECHO_RE.sub(" ", prompt)
    prompt = _ABSTRACT_CLAUSE_RE.sub("", prompt)
    prompt = _BANNED_ABSTRACT_TERMS_RE.sub("", prompt)
    prompt = re.sub(r"\{[^}]+\}", "", prompt)
    prompt = _ASPECT_OR_TECH_SUFFIX_RE.sub(" ", prompt)
    prompt = re.sub(r"\s*--ar\s*\d+:\d+", " ", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r"\s{2,}", " ", prompt).strip(" ,;")
    prompt = _strip_optimizer_preamble(prompt)

    if not prompt:
        return (
            "High-end editorial lifestyle photography, magazine cover style, "
            "clear central subject, soft dramatic lighting, shallow depth of field, "
            "shot on 35mm lens, sharp focus, authentic aesthetic"
        )
    return prompt


async def optimize_image_prompt_for_imagen(settings: Settings, raw: str) -> str:
    """LLM-оптимизатор Flux: перевод RU→EN + editorial lifestyle формулировка."""
    cleaned = sanitize_blogger_image_prompt_for_imagen(raw)
    if not settings.openrouter_key:
        return cleaned

    user_block = f"Input:\n{cleaned}"
    try:
        out = await ask_ai_messages(
            settings,
            [
                {"role": "system", "content": FLUX_OPTIMIZER_SYSTEM_PROMPT},
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
