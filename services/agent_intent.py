"""SMART_MODE: детекция intent генерации изображения через OpenRouter Function Calling."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from config import Settings, settings as default_settings
from content import messages as msg
from services.ai_text import ask_ai_messages
from services.billing.image_pipeline import normalize_image_model
from services.photo_aspect_ratio import normalize_photo_aspect_ratio

logger = logging.getLogger(__name__)

INTENT_MODEL = "google/gemini-2.5-flash"
INTENT_TIMEOUT_SEC = 8.0

TRIGGER_IMAGE_GENERATION = "trigger_image_generation"

from business_catalog import (
    FLUX_2_PRO_MODEL_KEY,
    GPT_IMAGE_2_MODEL_KEY,
    NANO_BANANA_2_MODEL_KEY,
)

DEFAULT_MODEL_KEY = NANO_BANANA_2_MODEL_KEY
DEFAULT_ASPECT_RATIO = "1:1"

_ASPECT_ENUM = ("1:1", "3:4", "4:5", "9:16", "16:9")
_MODEL_ENUM = (NANO_BANANA_2_MODEL_KEY, FLUX_2_PRO_MODEL_KEY, GPT_IMAGE_2_MODEL_KEY)

_SYSTEM_PROMPT = (
    "You route user messages for an AI bot. "
    "If the user asks to draw, paint, create, generate, depict, or visualize an image — "
    "call trigger_image_generation. "
    "Translate prompt to English. "
    f"Pick model_key: {FLUX_2_PRO_MODEL_KEY} for Flux/premium requests; "
    f"{GPT_IMAGE_2_MODEL_KEY} for GPT Image 2/OpenAI; "
    f"otherwise {NANO_BANANA_2_MODEL_KEY}. "
    "Pick aspect_ratio when user mentions vertical, horizontal, wallpaper, stories, 16:9, etc. "
    "Do NOT call the tool for normal chat unrelated to image generation."
)

# Быстрый pre-filter: без него каждый idle-текст уходит в OpenRouter (~8–12 с «тишины»).
_IMAGE_INTENT_HINT_RE = re.compile(
    r"(?:"
    r"нарис|рисун|картин|изображ|иллюстра|арт\b|"
    r"draw(?:ing)?|paint(?:ing)?|sketch|render|visuali[sz]e|"
    r"generate\s+(?:an?\s+)?(?:image|picture|photo|art)|"
    r"create\s+(?:an?\s+)?(?:image|picture|photo|art)|"
    r"picture\s+of|image\s+of|"
    r"dall[\s-]?e|midjourney|stable\s*diffusion|flux\b"
    r")",
    re.IGNORECASE,
)


def looks_like_image_generation_request(user_text: str) -> bool:
    """True только если текст похож на запрос генерации изображения."""
    text = (user_text or "").strip()
    if not text:
        return False
    return _IMAGE_INTENT_HINT_RE.search(text) is not None


def trigger_image_generation_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TRIGGER_IMAGE_GENERATION,
            "description": (
                "Start image generation when the user explicitly asks to create/draw an image."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Image description in English.",
                    },
                    "model_key": {
                        "type": "string",
                        "enum": list(_MODEL_ENUM),
                        "default": DEFAULT_MODEL_KEY,
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "enum": list(_ASPECT_ENUM),
                        "default": DEFAULT_ASPECT_RATIO,
                    },
                },
                "required": ["prompt"],
            },
        },
    }


def _model_label(model_id: str) -> str:
    normalized = normalize_image_model(model_id)
    for label, mid in msg.IMAGE_MODELS:
        if normalize_image_model(mid) == normalized:
            return label
    return normalized


def _resolve_model_from_key(model_key: str | None) -> tuple[str, str, str]:
    """Returns (model_key enum, canonical model_id, model_label)."""
    model_id = normalize_image_model(model_key or DEFAULT_MODEL_KEY)
    return model_id, model_id, _model_label(model_id)


def parse_trigger_image_generation_args(
    raw_arguments: str | dict[str, Any] | None,
) -> dict[str, Any] | None:
    payload: dict[str, Any] | None
    if isinstance(raw_arguments, dict):
        payload = raw_arguments
    else:
        text = (raw_arguments or "").strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("trigger_image_generation: invalid JSON")
            return None
        payload = parsed if isinstance(parsed, dict) else None

    if payload is None:
        return None

    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        return None

    model_key, model_id, model_label = _resolve_model_from_key(
        str(payload.get("model_key") or "")
    )
    aspect_ratio = normalize_photo_aspect_ratio(
        str(payload.get("aspect_ratio") or DEFAULT_ASPECT_RATIO)
    )

    return {
        "prompt": prompt,
        "model_key": model_key,
        "model_id": model_id,
        "model_label": model_label,
        "aspect_ratio": aspect_ratio,
    }


def extract_trigger_image_intent(tool_calls: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function")
        if not isinstance(fn, dict):
            continue
        if str(fn.get("name") or "").strip() != TRIGGER_IMAGE_GENERATION:
            continue
        parsed = parse_trigger_image_generation_args(fn.get("arguments"))
        if parsed is not None:
            return parsed
    return None


async def detect_image_intent(
    user_text: str,
    *,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """
    Быстрый запрос к Gemini Flash с tools.

    Returns:
        dict с prompt/model_id/model_label/aspect_ratio или None для обычного диалога.
    """
    text = (user_text or "").strip()
    if not text:
        return None

    cfg = settings or default_settings
    models: list[str] = []
    paid = (cfg.paid_text_model or "").strip()
    if paid:
        models.append(paid)
    if INTENT_MODEL not in models:
        models.append(INTENT_MODEL)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]

    try:
        async with asyncio.timeout(INTENT_TIMEOUT_SEC):
            result = await ask_ai_messages(
                cfg,
                messages,
                models=models,
                tools=[trigger_image_generation_tool()],
                tool_choice="auto",
                max_tokens=256,
                timeout=INTENT_TIMEOUT_SEC,
                max_context_chars=8_000,
                max_context_tokens=2_000,
                temperature=0.0,
            )
    except TimeoutError:
        logger.warning("agent_intent: detection timed out")
        return None
    except RuntimeError:
        logger.warning("agent_intent: OpenRouter unavailable")
        return None
    except Exception:
        logger.warning("agent_intent: detection failed", exc_info=True)
        return None

    return extract_trigger_image_intent(result.get("tool_calls"))
