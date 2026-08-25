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
    "energy_scales": {"capacity": 72, "immunity": 55, "scale": 81},
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
async def test_premium_report_skips_gemini_when_key_missing() -> None:
    with (
        patch.object(hd_logic, "genai", object()),
        patch.object(hd_logic, "_gemini_configured", return_value=False),
        patch.object(hd_logic, "_openrouter_configured", return_value=True),
        patch.object(
            hd_logic,
            "_generate_premium_via_gemini",
            new=AsyncMock(),
        ) as gemini_mock,
        patch.object(
            hd_logic,
            "_generate_premium_via_openrouter",
            new=AsyncMock(return_value=_SAMPLE_REPORT),
        ) as or_mock,
    ):
        report = await hd_logic.generate_premium_report("Генератор", "15.03.1990 14:30 Москва")
    assert report == _SAMPLE_REPORT
    gemini_mock.assert_not_awaited()
    or_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_premium_report_falls_back_when_gemini_raises() -> None:
    with (
        patch.object(hd_logic, "_gemini_configured", return_value=True),
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


@pytest.mark.parametrize(
    ("raw", "hd_type", "birth_data"),
    [
        ("12.09.1999, 5:20, город Москва", "не указан", "12.09.1999 5:20 Москва"),
        ("Генератор, 12.09.1999, 5:20, город Москва", "Генератор", "12.09.1999 5:20 Москва"),
        ("тип: Проектор\n12.09.1999, 5:20, г. Москва", "Проектор", "12.09.1999 5:20 Москва"),
        ("Манифестор 01.01.1990 10:00 в Санкт-Петербург", "Манифестор", "01.01.1990 10:00 Санкт-Петербург"),
        ("тип: Генератор, 15.03.1990, 14:30, Москва", "Генератор", "15.03.1990 14:30 Москва"),
    ],
)
def test_parse_hd_request_normalizes_user_input(raw: str, hd_type: str, birth_data: str) -> None:
    parsed_type, parsed_birth = hd_logic.parse_hd_request(raw)
    assert parsed_type == hd_type
    assert parsed_birth == birth_data
    assert hd_logic._extract_birth_numbers(parsed_birth) is not None


def test_premium_report_json_schema_version() -> None:
    raw = hd_logic.premium_report_to_json(_SAMPLE_REPORT)
    assert hd_logic.hd_report_schema_version(raw) == hd_logic._HD_REPORT_SCHEMA_VERSION
    assert hd_logic.is_legacy_hd_report_raw(raw) is False


def test_legacy_hd_report_detected_without_schema_version() -> None:
    legacy = hd_logic.premium_report_to_json(_SAMPLE_REPORT)
    import json

    payload = json.loads(legacy)
    payload.pop("schema_version", None)
    raw = json.dumps(payload, ensure_ascii=False)
    assert hd_logic.is_legacy_hd_report_raw(raw) is True


def test_strip_hd_markdown_for_plain() -> None:
    raw = "### Боль\n**1. Правило:** текст с **звёздочками** и `#` символом"
    clean = hd_logic.strip_hd_markdown_for_plain(raw)
    assert "###" not in clean
    assert "**" not in clean
    assert "1. Правило:" in clean
    assert "звёздочками" in clean


def test_format_premium_report_strips_markdown() -> None:
    report = dict(_SAMPLE_REPORT)
    report["money"] = "### Боль\n**Совет:** делай так."
    text = hd_logic.format_premium_report(report)
    assert "###" not in text
    assert "**" not in text
    assert "Совет:" in text


def test_elite_premium_prompt_forbids_type_guessing() -> None:
    system_prompt, user_prompt = hd_logic._build_elite_premium_hd_prompt(
        "Тест",
        {
            "hd_type": "Генератор",
            "birth_data": "15.03.1990 14:30 Москва",
            "defined_centers": ["Сакрал"],
            "open_centers": ["Корень"],
        },
    )
    assert hd_logic._ELITE_HD_SERVER_MATH_MANDATE in system_prompt
    assert "ЗАПРЕЩЕНО самостоятельно рассчитывать, угадывать или изменять тип" in system_prompt
    assert "НЕ УГАДЫВАЙ" not in system_prompt
    assert "эфемерид" not in system_prompt.lower()
    assert "если передан явно" not in user_prompt.lower()
    assert "energy_scales" in system_prompt
    assert "capacity" in system_prompt
    assert "БЕЗ markdown" in system_prompt
    assert "Генератор" in user_prompt
