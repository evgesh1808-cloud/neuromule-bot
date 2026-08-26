"""Полный HD-разбор: Gemini → OpenRouter fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from pathlib import Path

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
        patch.object(
            hd_logic,
            "_generate_premium_report_multipass",
            new=AsyncMock(side_effect=RuntimeError("multipass_synthesis_empty")),
        ),
        patch.object(hd_logic, "genai", None),
        patch.object(
            hd_logic,
            "_generate_premium_via_openrouter",
            new=AsyncMock(return_value=dict(_SAMPLE_REPORT)),
        ) as or_mock,
    ):
        report = await hd_logic.generate_premium_report("Генератор", "15.03.1990 14:30 Москва")
    assert report["money"] == "Финансовый блок"
    or_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_premium_report_skips_gemini_when_key_missing() -> None:
    with (
        patch.object(
            hd_logic,
            "_generate_premium_report_multipass",
            new=AsyncMock(side_effect=RuntimeError("multipass_synthesis_empty")),
        ),
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
            new=AsyncMock(return_value=dict(_SAMPLE_REPORT)),
        ) as or_mock,
    ):
        report = await hd_logic.generate_premium_report("Генератор", "15.03.1990 14:30 Москва")
    assert report["money"] == "Финансовый блок"
    gemini_mock.assert_not_awaited()
    or_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_premium_report_falls_back_when_gemini_raises() -> None:
    with (
        patch.object(
            hd_logic,
            "_generate_premium_report_multipass",
            new=AsyncMock(side_effect=RuntimeError("multipass_synthesis_empty")),
        ),
        patch.object(hd_logic, "_gemini_configured", return_value=True),
        patch.object(
            hd_logic,
            "_generate_premium_via_gemini",
            new=AsyncMock(side_effect=RuntimeError("gemini_unavailable: boom")),
        ),
        patch.object(
            hd_logic,
            "_generate_premium_via_openrouter",
            new=AsyncMock(return_value=dict(_SAMPLE_REPORT)),
        ) as or_mock,
        patch.object(hd_logic, "genai", object()),
    ):
        report = await hd_logic.generate_premium_report("Проектор", "01.01.2000 10:00 Казань")
    assert report["money"] == "Финансовый блок"
    or_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_premium_report_multipass_primary_path() -> None:
    multipass_report = dict(_SAMPLE_REPORT)
    multipass_report["static_reference"] = {"type": "Тип: Генератор"}
    multipass_report["synthesis_meta"] = {"blocks_ok": 3}
    with patch.object(
        hd_logic,
        "_generate_premium_report_multipass",
        new=AsyncMock(return_value=multipass_report),
    ) as mp_mock:
        report = await hd_logic.generate_premium_report("Генератор", "15.03.1990 14:30 Москва")
    assert report["synthesis_meta"]["blocks_ok"] == 3
    mp_mock.assert_awaited_once()


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
    assert "ГЕНЕТИЧЕСКИЙ СИНТЕЗ" in system_prompt
    assert "Генератор" in user_prompt
    assert "###" not in hd_logic._ELITE_HD_FEW_SHOT


def test_genetic_synthesis_prompt_schema_and_bans() -> None:
    math_data = {
        "profile": "3/5",
        "authority": "Сакральный",
        "strategy": "Ждать отклик",
        "definition": "Single",
        "active_channels": ["34-20"],
    }
    pair = {"open_center": "Эго", "anchors": ["Сакрал", "канал 34-20"]}
    scales = {"capacity": 72, "immunity": 55, "scale": 81}
    system_prompt, user_prompt = hd_logic._build_genetic_synthesis_prompt(
        domain="money",
        math_data=math_data,
        synthesis_pair=pair,
        energy_scales=scales,
    )
    assert "ТОТАЛЬНЫЙ ЗАПРЕТ НА ГАЛЛЮЦИНАЦИИ" in system_prompt
    assert hd_logic._GENETIC_SYNTHESIS_TEMPERATURE == 0.1
    assert "###" not in hd_logic._GENETIC_SYNTHESIS_FEW_SHOT
    assert "34-20" in user_prompt
    assert "capacity=72" in user_prompt
    assert "Domain): money" in user_prompt
    assert "Открытый центр [Эго]" in user_prompt
    assert "проживан" in system_prompt


def test_normalize_synthesis_response_valid_payload() -> None:
    payload = {
        "synthesis_anchor": "Открытое Эго × Сакрал, money.",
        "client_pain": "Боль в сфере денег.",
        "false_self_pattern": "Компенсация через перегруз.",
        "body_signal": "Напряжение в плечах.",
        "reflective_questions": ["Вопрос один?", "Вопрос два?", "Вопрос три?"],
        "experiments": [
            {
                "timeframe": "days_1-5",
                "action": "Записывать телесный отклик",
                "metric": "5 записей",
                "success_criteria": "Есть паттерн",
            },
            {
                "timeframe": "days_6-15",
                "action": "Отложить одно «да»",
                "metric": "1 кейс",
                "success_criteria": "Решение без спешки",
            },
            {
                "timeframe": "days_16-30",
                "action": "Интегрировать правило",
                "metric": "3 решения",
                "success_criteria": "Меньше напряжения",
            },
        ],
    }
    normalized = hd_logic._normalize_synthesis_response(payload)
    assert normalized["experiments"][0]["timeframe"] == "days_1-5"
    assert len(normalized["reflective_questions"]) == 3


def test_normalize_synthesis_response_rejects_markdown_headers() -> None:
    payload = {
        "synthesis_anchor": "### Ошибка",
        "client_pain": "x",
        "false_self_pattern": "x",
        "body_signal": "x",
        "reflective_questions": ["a", "b", "c"],
        "experiments": [
            {"action": "a", "metric": "m", "success_criteria": "s"},
            {"action": "a", "metric": "m", "success_criteria": "s"},
            {"action": "a", "metric": "m", "success_criteria": "s"},
        ],
    }
    with pytest.raises(ValueError, match="markdown headers"):
        hd_logic._normalize_synthesis_response(payload)


def test_build_synthesis_pairs_links_open_center_to_motors() -> None:
    math_data = {
        "defined_centers": ["Сакрал", "Горло"],
        "open_centers": ["Эго", "Корень"],
        "active_channels": ["34-20"],
    }
    pairs = hd_logic.build_synthesis_pairs(math_data)
    assert len(pairs) == 2
    ego_pair = next(item for item in pairs if item["open_center"] == "Эго")
    assert "Сакрал" in ego_pair["anchors"]


def test_derive_active_channels_requires_both_gates() -> None:
    assert hd_logic.derive_active_channels({34, 20}) == ["20-34"]
    assert hd_logic.derive_active_channels({34}) == []


@pytest.mark.skipif(hd_logic.swe is None, reason="pyswisseph not installed")
def test_build_hd_math_data_includes_synthesis_metadata() -> None:
    math_data = hd_logic.build_hd_math_data("Генератор", "15.03.1990 14:30 Москва")
    assert "active_channels" in math_data
    assert "definition" in math_data
    assert isinstance(math_data.get("synthesis_pairs"), list)


def test_compose_domain_chapter_merges_static_and_synthesis() -> None:
    synthesis = {
        "synthesis_anchor": "Эго × Сакрал",
        "client_pain": "Боль",
        "false_self_pattern": "Паттерн",
        "body_signal": "Плечи",
        "reflective_questions": ["Q1?", "Q2?", "Q3?"],
        "experiments": [
            {"timeframe": "days_1-5", "action": "A", "metric": "M", "success_criteria": "S"},
            {"timeframe": "days_6-15", "action": "A", "metric": "M", "success_criteria": "S"},
            {"timeframe": "days_16-30", "action": "A", "metric": "M", "success_criteria": "S"},
        ],
        "_pair": {"open_center": "Эго"},
    }
    chapter = hd_logic._compose_domain_chapter(
        "money",
        static_context="Статический контекст",
        synthesis_blocks=[synthesis],
    )
    assert "Статический контекст" in chapter
    assert "Эго" in chapter
    assert "Боль" in chapter


def test_premium_report_json_includes_static_reference() -> None:
    report = dict(_SAMPLE_REPORT)
    report["static_reference"] = {"type": "Тип: Генератор"}
    raw = hd_logic.premium_report_to_json(report)
    import json

    payload = json.loads(raw)
    assert payload["schema_version"] == 3
    assert payload["static_reference"]["type"] == "Тип: Генератор"


def test_md_to_reportlab_html_bold() -> None:
    html = hd_logic._md_to_reportlab_html("**Боль:** текст")
    assert "<b>" in html
    assert "**" not in html


def test_create_hd_premium_pdf_multipage(tmp_path, monkeypatch) -> None:
    if hd_logic.BaseDocTemplate is None:
        pytest.skip("reportlab not installed")
    out_dir = tmp_path / "tmp"
    out_dir.mkdir()
    monkeypatch.setattr(hd_logic, "_HD_BODYGRAPH_OUTPUT_DIR", out_dir)
    monkeypatch.setattr(hd_logic, "_HD_BODYGRAPH_TEMPLATE_PATH", out_dir / "missing.png")
    report = dict(_SAMPLE_REPORT)
    report["money"] = "**Боль:** длинный блок " * 40
    path = hd_logic.create_hd_premium_pdf(
        777,
        report,
        "15.03.1990 14:30 Москва",
        hd_type="Генератор",
        user_name="Тест",
    )
    pdf_file = Path(path)
    assert pdf_file.is_file()
    assert pdf_file.stat().st_size > 1500


@pytest.mark.skipif(hd_logic.swe is None, reason="pyswisseph not installed")
def test_build_hd_math_data_derives_type_and_profile_when_missing() -> None:
    math_data = hd_logic.build_hd_math_data("не указан", "15.03.1990 14:30 Москва")
    assert math_data["hd_type"] not in {"", "не указан"}
    assert math_data.get("profile")
    assert math_data.get("authority")
    assert math_data.get("strategy")
    meta = hd_logic.hd_profile_metadata(math_data)
    assert meta["hd_type"] not in {"", "—"}
    assert meta["profile"] not in {"", "—"}


@pytest.mark.skipif(hd_logic.swe is None, reason="pyswisseph not installed")
def test_derive_hd_chart_from_birth_returns_strategy_for_type() -> None:
    chart = hd_logic.derive_hd_chart_from_birth("15.03.1990 14:30 Москва")
    assert chart["hd_type"]
    assert chart["strategy"]
    assert "/" in chart["profile"]


def test_generate_instagram_stories_writes_two_cards(tmp_path, monkeypatch) -> None:
    if hd_logic.Image is None:
        pytest.skip("Pillow not installed")
    out_dir = tmp_path / "tmp"
    monkeypatch.setattr(hd_logic, "_HD_BODYGRAPH_OUTPUT_DIR", out_dir)
    math_data = {
        "hd_type": "Генератор",
        "birth_data": "15.03.1990 14:30 Москва",
        "profile": "3/5",
        "authority": "Сакральный",
        "strategy": "Ждать отклик",
    }
    report = dict(_SAMPLE_REPORT)
    report["money"] = "Подробный блок про деньги " * 20
    paths = hd_logic.generate_instagram_stories(999, report, math_data=math_data)
    assert len(paths) == 2
    assert (out_dir / "story_999_1.png").is_file()
    assert (out_dir / "story_999_2.png").is_file()
