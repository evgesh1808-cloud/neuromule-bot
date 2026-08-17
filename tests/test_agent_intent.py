"""SMART_MODE agent_intent и dispatch."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content import messages as msg
from services.agent_intent import (
    TRIGGER_IMAGE_GENERATION,
    detect_image_intent,
    looks_like_image_generation_request,
    parse_trigger_image_generation_args,
    trigger_image_generation_tool,
)
from services.agent_intent_dispatch import (
    format_agent_image_ack,
    try_agent_image_intent_telegram,
)


def test_trigger_image_generation_tool_schema() -> None:
    tool = trigger_image_generation_tool()
    fn = tool["function"]
    assert fn["name"] == TRIGGER_IMAGE_GENERATION
    props = fn["parameters"]["properties"]
    assert props["model_key"]["default"] == "nano_banana_2"
    assert "gpt_image_2" in props["model_key"]["enum"]


def test_parse_trigger_defaults_to_nano_banana2() -> None:
    raw = json.dumps({"prompt": "A cat on the moon"})
    parsed = parse_trigger_image_generation_args(raw)
    assert parsed is not None
    assert parsed["model_id"] == "nano_banana_2"
    assert parsed["model_key"] == "nano_banana_2"


def test_parse_trigger_flux_and_gpt() -> None:
    flux = parse_trigger_image_generation_args(
        json.dumps({"prompt": "Epic landscape", "model_key": "flux-schnell", "aspect_ratio": "16:9"})
    )
    assert flux is not None
    assert flux["model_id"] == "flux_2_pro"

    gpt = parse_trigger_image_generation_args(
        json.dumps({"prompt": "Logo design", "model_key": "gpt_image2"})
    )
    assert gpt is not None
    assert gpt["model_id"] == "gpt_image_2"


@pytest.mark.asyncio
async def test_detect_image_intent_returns_dict() -> None:
    tool_calls = [
        {
            "function": {
                "name": TRIGGER_IMAGE_GENERATION,
                "arguments": json.dumps(
                    {
                        "prompt": "Sunset beach",
                        "model_key": "nano_banana2",
                        "aspect_ratio": "9:16",
                    }
                ),
            }
        }
    ]
    with patch(
        "services.agent_intent.ask_ai_messages",
        new_callable=AsyncMock,
        return_value={"content": "", "prompt_tokens": 1, "completion_tokens": 1, "tool_calls": tool_calls},
    ):
        intent = await detect_image_intent("нарисуй закат на пляже вертикально")

    assert intent is not None
    assert intent["prompt"] == "Sunset beach"
    assert intent["aspect_ratio"] == "9:16"


@pytest.mark.asyncio
async def test_try_agent_intent_skips_non_idle_fsm() -> None:
    message = MagicMock()
    message.from_user.id = 1
    message.text = "нарисуй кота"

    state = MagicMock()
    state.get_state = AsyncMock(return_value="UserFlow:waiting_for_photo")

    with patch("services.agent_intent_dispatch.detect_image_intent", new_callable=AsyncMock) as detect:
        handled = await try_agent_image_intent_telegram(message, state)

    assert handled is False
    detect.assert_not_awaited()


@pytest.mark.asyncio
async def test_try_agent_intent_refuses_insufficient_balance() -> None:
    message = MagicMock()
    message.from_user.id = 2
    message.text = "создай картинку заката"
    message.answer = AsyncMock()

    state = MagicMock()
    state.get_state = AsyncMock(return_value=None)

    intent = {
        "prompt": "Sunset",
        "model_id": "nano_banana2",
        "model_label": "Nano Banana 2",
        "aspect_ratio": "1:1",
    }

    with patch(
        "services.agent_intent_dispatch.detect_image_intent",
        new_callable=AsyncMock,
        return_value=intent,
    ), patch(
        "services.agent_intent_dispatch.preview_image_affordability",
        new_callable=AsyncMock,
        return_value=(False, msg.TXT_INSUFFICIENT_BALANCE),
    ):
        handled = await try_agent_image_intent_telegram(message, state)

    assert handled is True
    message.answer.assert_awaited()


def test_format_agent_image_ack() -> None:
    text = format_agent_image_ack("Flux 2 Pro", "16:9")
    assert "Flux 2 Pro" in text
    assert "16:9" in text


def test_looks_like_image_generation_request() -> None:
    assert looks_like_image_generation_request("нарисуй закат на море") is True
    assert looks_like_image_generation_request("draw a cat in space") is True
    assert looks_like_image_generation_request("привет, как дела?") is False
    assert looks_like_image_generation_request("расскажи про Python") is False


@pytest.mark.asyncio
async def test_try_agent_intent_skips_plain_chat_without_openrouter() -> None:
    message = MagicMock()
    message.from_user.id = 3
    message.text = "привет, как дела?"
    message.answer = AsyncMock()

    state = MagicMock()
    state.get_state = AsyncMock(return_value=None)

    with patch("services.agent_intent_dispatch.detect_image_intent", new_callable=AsyncMock) as detect:
        handled = await try_agent_image_intent_telegram(message, state)

    assert handled is False
    detect.assert_not_awaited()
