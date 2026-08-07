"""OpenRouter Function Calling: инструменты SMART_MODE агента."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from content import messages as msg
from services.billing.image_pipeline import normalize_image_model
from services.photo_aspect_ratio import normalize_photo_aspect_ratio

logger = logging.getLogger(__name__)

GENERATE_IMAGE_TOOL_NAME = "generate_image"
DEFAULT_SMART_IMAGE_MODEL = "flux-schnell"
DEFAULT_SMART_IMAGE_ASPECT = "1:1"

_SMART_MODE_ASPECTS = ("1:1", "3:4", "4:5", "9:16", "16:9")


@dataclass(frozen=True, slots=True)
class GenerateImageToolArgs:
    prompt: str
    model_id: str
    model_label: str
    aspect_ratio: str


def generate_image_openrouter_tool() -> dict[str, Any]:
    """Схема ``generate_image`` для OpenRouter ``tools``."""
    return {
        "type": "function",
        "function": {
            "name": GENERATE_IMAGE_TOOL_NAME,
            "description": (
                "Generate an image from a text prompt. Use when the user asks to draw, paint, "
                "create, depict, or visualize an image, picture, photo, or illustration."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": (
                            "Detailed image generation prompt in English "
                            "(translate from user language if needed)."
                        ),
                    },
                    "model": {
                        "type": "string",
                        "description": "Image model id.",
                        "default": DEFAULT_SMART_IMAGE_MODEL,
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "description": "Aspect ratio of the output image.",
                        "enum": list(_SMART_MODE_ASPECTS),
                        "default": DEFAULT_SMART_IMAGE_ASPECT,
                    },
                },
                "required": ["prompt"],
            },
        },
    }


def smart_agent_openrouter_tools() -> list[dict[str, Any]]:
    """Список tools для SMART_MODE роутера."""
    return [generate_image_openrouter_tool()]


def _allowed_smart_image_models() -> frozenset[str]:
    return frozenset(normalize_image_model(mid) for _, mid in msg.IMAGE_MODELS)


def resolve_smart_image_model(model: str | None) -> tuple[str, str]:
    """Нормализует model id и возвращает пару (id, label) для FSM/биллинга."""
    raw = (model or DEFAULT_SMART_IMAGE_MODEL).strip()
    normalized = normalize_image_model(raw)
    allowed = _allowed_smart_image_models()
    if normalized not in allowed:
        normalized = normalize_image_model(DEFAULT_SMART_IMAGE_MODEL)
    label = next(
        (lbl for lbl, mid in msg.IMAGE_MODELS if normalize_image_model(mid) == normalized),
        normalized,
    )
    return normalized, label


def parse_generate_image_tool_args(raw_arguments: str | dict[str, Any] | None) -> GenerateImageToolArgs | None:
    """Парсит JSON-аргументы tool call ``generate_image``."""
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
            logger.warning("generate_image tool: invalid JSON arguments")
            return None
        payload = parsed if isinstance(parsed, dict) else None

    if payload is None:
        return None

    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        return None

    model_id, model_label = resolve_smart_image_model(str(payload.get("model") or ""))
    aspect_ratio = normalize_photo_aspect_ratio(str(payload.get("aspect_ratio") or DEFAULT_SMART_IMAGE_ASPECT))
    return GenerateImageToolArgs(
        prompt=prompt,
        model_id=model_id,
        model_label=model_label,
        aspect_ratio=aspect_ratio,
    )


def extract_generate_image_from_tool_calls(tool_calls: list[dict[str, Any]] | None) -> GenerateImageToolArgs | None:
    """Ищет первый вызов ``generate_image`` в ответе OpenRouter."""
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function")
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "").strip()
        if name != GENERATE_IMAGE_TOOL_NAME:
            continue
        args = parse_generate_image_tool_args(fn.get("arguments"))
        if args is not None:
            return args
    return None
