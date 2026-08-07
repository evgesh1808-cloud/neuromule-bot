"""Unit-тесты парсера aspect intent для multi-turn i2i."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.photo_intent_parser import (
    coerce_parsed_image_intent,
    parse_image_intent,
    resolve_photo_edit_prompt,
)


def test_coerce_intent_extracts_aspect_and_clean_prompt() -> None:
    raw = '{"aspect_ratio": "9:16", "clean_prompt": "добавь закат"}'
    aspect, clean = coerce_parsed_image_intent(raw, fallback_prompt="сделай stories 9:16 закат")
    assert aspect == "9:16"
    assert clean == "добавь закат"


def test_coerce_intent_null_aspect_keeps_fallback() -> None:
    raw = '{"aspect_ratio": null, "clean_prompt": "улучши освещение"}'
    aspect, clean = coerce_parsed_image_intent(raw, fallback_prompt="улучши освещение")
    assert aspect is None
    assert clean == "улучши освещение"


def test_coerce_intent_invalid_aspect_is_ignored() -> None:
    raw = '{"aspect_ratio": "9:21", "clean_prompt": "широкий кадр"}'
    aspect, clean = coerce_parsed_image_intent(raw, fallback_prompt="широкий кадр")
    assert aspect is None
    assert clean == "широкий кадр"


@pytest.mark.asyncio
async def test_parse_image_intent_openrouter_failure_returns_fallback() -> None:
    with patch(
        "services.photo_intent_parser.ask_ai_messages",
        new_callable=AsyncMock,
        side_effect=RuntimeError("openrouter_unavailable"),
    ):
        aspect, clean = await parse_image_intent("сделай 16:9 и добавь неон")
    assert aspect is None
    assert clean == "сделай 16:9 и добавь неон"


@pytest.mark.asyncio
async def test_resolve_photo_edit_prompt_updates_aspect_when_detected() -> None:
    with patch(
        "services.photo_intent_parser.parse_image_intent",
        new_callable=AsyncMock,
        return_value=("4:5", "instagram look"),
    ):
        aspect, prompt, changed = await resolve_photo_edit_prompt(
            "переделай в instagram 4:5",
            current_aspect="1:1",
        )
    assert changed is True
    assert aspect == "4:5"
    assert prompt == "instagram look"


@pytest.mark.asyncio
async def test_resolve_photo_edit_prompt_keeps_session_aspect_when_not_detected() -> None:
    with patch(
        "services.photo_intent_parser.parse_image_intent",
        new_callable=AsyncMock,
        return_value=(None, "добавь туман"),
    ):
        aspect, prompt, changed = await resolve_photo_edit_prompt(
            "добавь туман",
            current_aspect="16:9",
        )
    assert changed is False
    assert aspect == "16:9"
    assert prompt == "добавь туман"
