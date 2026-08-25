"""Полный HD-разбор: Gemini → OpenRouter fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services import hd_logic


_SAMPLE_REPORT = {
    "fast_facts": "⚡ Главный баг прошивки: спешка. 💼 Триггер денег: отклик тела. 🔋 Перезагрузка: сон.",
    "money": "Финансовый блок",
    "love": "Блок отношений",
    "energy": "Энергетический блок",
    "plan": "План на 30 дней",
}


@pytest.mark.asyncio
async def test_premium_report_falls_back_to_openrouter_when_gemini_missing() -> None:
    with (
        patch.object(hd_logic, "genai", None),
        patch.object(
            hd_logic,
            "_generate_premium_via_openrouter",
            new=AsyncMock(return_value=_SAMPLE_REPORT),
        ) as or_mock,
    ):
        report = await hd_logic.generate_premium_report("Генератор", "15.03.1990 14:30 Москва")
    assert report == _SAMPLE_REPORT
    or_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_premium_report_falls_back_when_gemini_raises() -> None:
    with (
        patch.object(
            hd_logic,
            "_generate_premium_via_gemini",
            new=AsyncMock(side_effect=RuntimeError("gemini_unavailable: boom")),
        ),
        patch.object(
            hd_logic,
            "_generate_premium_via_openrouter",
            new=AsyncMock(return_value=_SAMPLE_REPORT),
        ) as or_mock,
        patch.object(hd_logic, "genai", object()),
    ):
        report = await hd_logic.generate_premium_report("Проектор", "01.01.2000 10:00 Казань")
    assert report["money"] == "Финансовый блок"
    or_mock.assert_awaited_once()
