"""Gemini → OpenRouter fallback для «Совета дня»."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services import hd_logic


@pytest.mark.asyncio
async def test_daily_advice_falls_back_to_openrouter_when_gemini_missing() -> None:
    profile = {
        "hd_type": "Генератор",
        "user_role": "предприниматель",
        "birth_date": "14.05.1990",
        "birth_time": "14:35",
        "birth_place": "Москва",
    }
    with (
        patch.object(hd_logic, "genai", None),
        patch.object(
            hd_logic,
            "_generate_daily_via_openrouter",
            new=AsyncMock(return_value="🔮 Совет через OpenRouter\n\nCTA"),
        ) as or_mock,
    ):
        text = await hd_logic.generate_daily_forecast(profile, current_cta_text="CTA")
    assert "OpenRouter" in text
    or_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_daily_advice_falls_back_when_gemini_raises() -> None:
    profile = {
        "hd_type": "Проектор",
        "user_role": "мама",
        "birth_date": "01.01.2000",
        "birth_time": "10:00",
        "birth_place": "Казань",
    }
    with (
        patch.object(
            hd_logic,
            "_generate_daily_via_gemini",
            new=AsyncMock(side_effect=RuntimeError("gemini_unavailable: boom")),
        ),
        patch.object(
            hd_logic,
            "_generate_daily_via_openrouter",
            new=AsyncMock(return_value="fallback text ok"),
        ) as or_mock,
        patch.object(hd_logic, "genai", object()),
    ):
        text = await hd_logic.generate_daily_forecast(profile, current_cta_text="x")
    assert text == "fallback text ok"
    or_mock.assert_awaited_once()


def test_extract_gemini_text_handles_property_error() -> None:
    class _Bad:
        @property
        def text(self) -> str:
            raise ValueError("blocked")

        candidates = ()

    assert hd_logic._extract_gemini_text(_Bad()) == ""


def test_extract_gemini_text_from_parts() -> None:
    class _Part:
        text = "hello"

    class _Content:
        parts = [_Part()]

    class _Cand:
        content = _Content()

    class _Resp:
        @property
        def text(self) -> str:
            raise ValueError("blocked")

        candidates = [_Cand()]

    assert hd_logic._extract_gemini_text(_Resp()) == "hello"
