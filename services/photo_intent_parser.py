"""Извлечение aspect ratio и очищенного промпта из текста multi-turn i2i."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from config import Settings, settings as default_settings
from services.ai_text import ask_ai_messages
from services.photo_aspect_ratio import normalize_photo_aspect_ratio

logger = logging.getLogger(__name__)

INTENT_PARSER_MODEL = "google/gemini-3.1-flash"
PHOTO_INTENT_TIMEOUT_SEC = 12.0

_SYSTEM_INSTRUCTION = (
    "Проанализируй запрос пользователя для редактирования картинки. "
    "Если пользователь просит изменить формат/соотношение сторон, выдели его и верни "
    "строго одно из значений: '1:1', '3:4', '4:5', '9:16', '16:9'. "
    "Если просьбы изменить формат нет, верни null. "
    "Также верни очищенный текст промпта без упоминания формата. "
    "Ответ верни в формате JSON: {'aspect_ratio': '...', 'clean_prompt': '...'}"
)


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        text = text.removesuffix("```").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def coerce_parsed_image_intent(
    raw_content: str,
    *,
    fallback_prompt: str,
) -> tuple[str | None, str]:
    """Чистая нормализация JSON-ответа LLM (для unit-тестов и runtime)."""
    fallback = (fallback_prompt or "").strip()
    payload = _extract_json_object(raw_content)
    if payload is None:
        return None, fallback

    aspect_raw = payload.get("aspect_ratio")
    aspect: str | None = None
    if aspect_raw is not None and str(aspect_raw).strip().lower() not in {
        "",
        "null",
        "none",
    }:
        normalized = normalize_photo_aspect_ratio(str(aspect_raw))
        original = str(aspect_raw).strip()
        if original in {"1:1", "3:4", "4:5", "9:16", "16:9"}:
            aspect = normalized

    clean_raw = payload.get("clean_prompt")
    clean = str(clean_raw).strip() if clean_raw is not None else ""
    if not clean:
        clean = fallback
    return aspect, clean


async def parse_image_intent(
    user_text: str,
    *,
    settings: Settings | None = None,
) -> tuple[str | None, str]:
    """
    Быстрый системный запрос к OpenRouter (Gemini Flash).

    Returns:
        (detected_aspect_or_none, clean_prompt)
    """
    prompt = (user_text or "").strip()
    if not prompt:
        return None, ""

    cfg = settings or default_settings
    model_chain = [INTENT_PARSER_MODEL]
    if cfg.paid_text_model and cfg.paid_text_model not in model_chain:
        model_chain.append(cfg.paid_text_model)

    messages = [
        {"role": "system", "content": _SYSTEM_INSTRUCTION},
        {"role": "user", "content": prompt},
    ]

    try:
        async with asyncio.timeout(PHOTO_INTENT_TIMEOUT_SEC):
            result = await ask_ai_messages(
                cfg,
                messages,
                models=model_chain,
                max_tokens=256,
                timeout=PHOTO_INTENT_TIMEOUT_SEC,
                max_context_chars=8_000,
                max_context_tokens=2_000,
                response_format={"type": "json_object"},
                temperature=0.0,
            )
    except TimeoutError:
        logger.warning("photo intent parse timed out")
        return None, prompt
    except RuntimeError:
        logger.warning("photo intent parse: OpenRouter unavailable")
        return None, prompt
    except Exception:
        logger.warning("photo intent parse failed", exc_info=True)
        return None, prompt

    return coerce_parsed_image_intent(
        result.get("content") or "",
        fallback_prompt=prompt,
    )


async def resolve_photo_edit_prompt(
    user_text: str,
    *,
    current_aspect: str,
    settings: Settings | None = None,
) -> tuple[str, str, bool]:
    """
    Парсит intent и возвращает финальный aspect, промпт и флаг смены формата.

    Returns:
        (aspect_ratio, clean_prompt, aspect_changed)
    """
    detected, clean = await parse_image_intent(user_text, settings=settings)
    changed = detected is not None
    aspect = (
        normalize_photo_aspect_ratio(detected)
        if changed
        else normalize_photo_aspect_ratio(current_aspect)
    )
    prompt = (clean or user_text).strip()
    return aspect, prompt, changed
