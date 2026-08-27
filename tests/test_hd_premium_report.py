"""Полный HD-разбор: Gemini → OpenRouter fallback."""

from __future__ import annotations

import json
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
        patch.object(
            hd_logic,
            "_generate_premium_report_multipass_legacy",
            new=AsyncMock(side_effect=RuntimeError("legacy_skip")),
        ),
        patch.object(hd_logic, "genai", None),
        patch.object(hd_logic, "_openrouter_configured", return_value=True),
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
        patch.object(
            hd_logic,
            "_generate_premium_report_multipass_legacy",
            new=AsyncMock(side_effect=RuntimeError("legacy_skip")),
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
        patch.object(
            hd_logic,
            "_generate_premium_report_multipass_legacy",
            new=AsyncMock(side_effect=RuntimeError("legacy_skip")),
        ),
        patch.object(hd_logic, "_openrouter_configured", return_value=True),
        patch.object(
            hd_logic,
            "_generate_premium_via_openrouter",
            new=AsyncMock(side_effect=RuntimeError("or_down")),
        ),
        patch.object(hd_logic, "_gemini_configured", return_value=True),
        patch.object(
            hd_logic,
            "_generate_premium_via_gemini",
            new=AsyncMock(return_value=dict(_SAMPLE_REPORT)),
        ) as gemini_mock,
        patch.object(hd_logic, "genai", object()),
    ):
        report = await hd_logic.generate_premium_report("Проектор", "01.01.2000 10:00 Казань")
    assert report["money"] == "Финансовый блок"
    gemini_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_premium_report_upgrade_mode_uses_multipass() -> None:
    mp_report = dict(_SAMPLE_REPORT)
    mp_report["synthesis_meta"] = {"parallel_domains": True, "blocks_ok": 3}
    with (
        patch.object(
            hd_logic,
            "_generate_premium_report_multipass",
            new=AsyncMock(return_value=mp_report),
        ) as mp_mock,
        patch.object(
            hd_logic,
            "_generate_premium_report_upgrade_fast",
            new=AsyncMock(),
        ) as fast_mock,
    ):
        report = await hd_logic.generate_premium_report(
            "Генератор",
            "15.03.1990 14:30 Москва",
            upgrade_mode=True,
        )
    assert report["synthesis_meta"]["blocks_ok"] == 3
    mp_mock.assert_awaited_once()
    fast_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_premium_report_multipass_primary_path() -> None:
    multipass_report = dict(_SAMPLE_REPORT)
    multipass_report["static_reference"] = {"type": "Тип: Генератор"}
    multipass_report["synthesis_meta"] = {"blocks_ok": 3, "llm_calls": 4, "parallel_domains": True}
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


def test_schema_v3_with_placeholder_is_not_legacy() -> None:
    raw = hd_logic.premium_report_to_json(_SAMPLE_REPORT)
    import json

    payload = json.loads(raw)
    payload["fast_facts"] = hd_logic._LEGACY_HD_REPORT_PLACEHOLDER
    payload["schema_version"] = hd_logic._HD_REPORT_SCHEMA_VERSION
    raw_v3 = json.dumps(payload, ensure_ascii=False)
    assert hd_logic.hd_report_schema_version(raw_v3) == hd_logic._HD_REPORT_SCHEMA_VERSION
    assert hd_logic.is_legacy_hd_report_raw(raw_v3) is False


def test_parse_plain_text_hd_report_storage() -> None:
    plain = hd_logic.format_premium_report(_SAMPLE_REPORT)
    parsed = hd_logic._parse_hd_report_storage(plain)
    assert parsed.get("money")
    assert parsed.get("love")


@pytest.mark.asyncio
async def test_ensure_modern_hd_report_offline_when_llm_fails(monkeypatch) -> None:
    legacy = hd_logic.premium_report_to_json(_SAMPLE_REPORT)
    import json

    payload = json.loads(legacy)
    payload.pop("schema_version", None)
    raw = json.dumps(payload, ensure_ascii=False)

    async def _fake_get_user(_uid: int):
        class _Row:
            def keys(self):
                return ("hd_report_json", "hd_birth_data", "hd_type")

            def __getitem__(self, key: str):
                data = {
                    "hd_report_json": raw,
                    "hd_birth_data": "15.03.1990 14:30 Москва",
                    "hd_type": "Генератор",
                }
                return data[key]

        return _Row()

    monkeypatch.setattr(hd_logic, "get_user", _fake_get_user)
    monkeypatch.setattr(hd_logic, "update_user", AsyncMock())
    monkeypatch.setattr(
        hd_logic,
        "generate_premium_report",
        AsyncMock(side_effect=RuntimeError("openrouter_unavailable")),
    )
    monkeypatch.setattr(hd_logic, "generate_premium_bodygraph", lambda *a, **k: "")

    report, upgraded = await hd_logic.ensure_modern_hd_report(435041303, user_name="Тест")
    assert upgraded is True
    assert report is not None
    assert report.get("money")


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
    assert "Фрактальн" in system_prompt
    assert "ЗАПРЕТ АНГЛИЦИЗМОВ" in system_prompt
    assert hd_logic._GENETIC_SYNTHESIS_TEMPERATURE == 0.1
    assert "###" not in hd_logic._GENETIC_SYNTHESIS_FEW_SHOT
    assert "34-20" in user_prompt
    assert "capacity=72" in user_prompt
    assert "Сфера жизни: money" in user_prompt
    assert "Архетип для пользователя" in user_prompt
    assert "Экспериментатор-Спасатель" in user_prompt
    assert "Суперсила влияния в моменте" in user_prompt
    assert "Эффект Зеркала" in system_prompt or "ЭФФЕКТ ЗЕРКАЛА" in system_prompt
    assert "Открытый центр [Эго]" in user_prompt
    assert "проживан" in system_prompt


def test_normalize_synthesis_response_rejects_anglicisms() -> None:
    payload = {
        "synthesis_anchor": "Struggle channel pattern",
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
    with pytest.raises(ValueError, match="banned markers"):
        hd_logic._normalize_synthesis_response(payload)


def test_normalize_synthesis_response_rejects_raw_channel_code() -> None:
    payload = {
        "synthesis_anchor": "Ловушка канала 20-34",
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
    with pytest.raises(ValueError, match="raw channel code"):
        hd_logic._normalize_synthesis_response(payload)


def test_openrouter_model_cascades(monkeypatch) -> None:
    monkeypatch.setattr(hd_logic, "_hd_premium_llm_tier", lambda: "production")
    assert hd_logic._openrouter_models_for_premium() == [
        "anthropic/claude-3.5-sonnet",
        "deepseek/deepseek-r1",
    ]
    assert hd_logic._openrouter_models_for_premium_upgrade() == [
        "anthropic/claude-3.5-sonnet",
        "deepseek/deepseek-r1",
    ]
    monkeypatch.setattr(hd_logic, "_hd_premium_llm_tier", lambda: "economy")
    assert hd_logic._openrouter_models_for_premium() == [
        "google/gemini-2.5-flash",
        "google/gemini-3.1-pro-preview",
        "deepseek/deepseek-chat",
    ]
    assert hd_logic._openrouter_models_for_premium_upgrade()[0] == "google/gemini-2.5-flash"


def test_gemini_premium_model_chain_uses_current_models() -> None:
    assert hd_logic._GEMINI_PREMIUM_MODEL_CHAIN[0] == "gemini-3.1-pro-preview"
    assert "gemini-1.5-pro-latest" not in hd_logic._GEMINI_PREMIUM_MODEL_CHAIN
    assert "gemini-2.0-pro-exp-02-15" not in hd_logic._GEMINI_PREMIUM_MODEL_CHAIN


@pytest.mark.asyncio
async def test_premium_report_multipass_failure_falls_back_to_legacy() -> None:
    legacy_report = dict(_SAMPLE_REPORT)
    with (
        patch.object(
            hd_logic,
            "_generate_premium_report_multipass",
            new=AsyncMock(side_effect=RuntimeError("multipass_synthesis_empty")),
        ),
        patch.object(
            hd_logic,
            "_generate_premium_report_multipass_legacy",
            new=AsyncMock(side_effect=RuntimeError("legacy_skip")),
        ),
        patch.object(
            hd_logic,
            "_generate_premium_report_legacy_single_prompt",
            new=AsyncMock(return_value=legacy_report),
        ) as legacy_mock,
    ):
        report = await hd_logic.generate_premium_report(
            "Генератор",
            "15.03.1990 14:30 Москва",
            upgrade_mode=True,
        )
    assert report == legacy_report
    legacy_mock.assert_awaited_once()


def test_wrap_legacy_report_as_v3_preserves_sections() -> None:
    raw = json.dumps(
        {
            "schema_version": 1,
            "money": "Старый блок про деньги",
            "love": "Старый блок про любовь",
            "energy": "Старый блок про энергию",
            "plan": "Старый план",
        },
        ensure_ascii=False,
    )
    math_data = hd_logic.build_hd_math_data("Генератор", "15.03.1990 14:30 Москва")
    report = hd_logic._wrap_legacy_report_as_v3(raw, math_data)
    assert report["money"] == "Старый блок про деньги"
    assert report["synthesis_meta"]["upgrade_offline"] is True
    assert "energy_scales" in report


def test_sanitize_hd_user_facing_text_replaces_profile_code() -> None:
    raw = "Твой профиль 3/5 — это про кризисы."
    cleaned = hd_logic._sanitize_hd_user_facing_text(raw)
    assert "3/5" not in cleaned
    assert "Экспериментатор-Спасатель" in cleaned
    assert hd_logic._openrouter_models_for_welcome_hook() == [
        "openai/gpt-4o",
        "deepseek/deepseek-chat",
    ]


def test_normalize_synthesis_response_rejects_raw_profile_code() -> None:
    payload = {
        "synthesis_anchor": "Ловушка для профиля 3/5",
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
    with pytest.raises(ValueError, match="raw profile code"):
        hd_logic._normalize_synthesis_response(payload)


def test_normalize_synthesis_response_valid_payload() -> None:
    payload = {
        "synthesis_anchor": "Открытое Эго × Сакрал — финансовая сфера.",
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
    assert payload["schema_version"] == 4
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
    assert pdf_file.parent == out_dir
    assert pdf_file.stat().st_size > 1500


def test_create_hd_premium_pdf_strips_emoji_and_fallback(tmp_path, monkeypatch) -> None:
    if hd_logic.BaseDocTemplate is None:
        pytest.skip("reportlab not installed")
    out_dir = tmp_path / "tmp"
    out_dir.mkdir()
    monkeypatch.setattr(hd_logic, "_HD_BODYGRAPH_OUTPUT_DIR", out_dir)
    monkeypatch.setattr(hd_logic, "_HD_BODYGRAPH_TEMPLATE_PATH", out_dir / "missing.png")
    report = dict(_SAMPLE_REPORT)
    report["fast_facts"] = "⚡ emoji " * 20
    real_build = hd_logic._build_hd_premium_pdf_story

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated full pdf failure")

    monkeypatch.setattr(hd_logic, "_build_hd_premium_pdf_story", _boom)
    path = hd_logic.create_hd_premium_pdf(
        888,
        report,
        "15.03.1990 14:30 Москва",
        hd_type="Генератор",
        user_name="Тест",
    )
    assert Path(path).is_file()
    _ = real_build


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
    assert hd_logic._STORY_FONT_REGULAR_PATH.is_file()
    assert hd_logic._STORY_FONT_BOLD_PATH.is_file()
    font = hd_logic._load_story_font(24)
    assert font is not None
    assert font.getbbox("Кириллица")[2] > font.getbbox("A")[2]
    out_dir = tmp_path / "tmp"
    monkeypatch.setattr(hd_logic, "_HD_BODYGRAPH_OUTPUT_DIR", out_dir)
    math_data = {
        "hd_type": "Генератор",
        "birth_data": "15.03.1990 14:30 Москва",
        "profile": "3/5",
        "authority": "Сакральный",
        "strategy": "Ждать отклик",
        "active_channels": ["20-34", "10-34", "34-57"],
    }
    report = dict(_SAMPLE_REPORT)
    paths = hd_logic.generate_instagram_stories(999, report, math_data=math_data)
    assert len(paths) == 2
    assert (out_dir / "story_999_1.jpg").is_file()
    assert (out_dir / "story_999_2.jpg").is_file()
    sections = hd_logic._build_story_card2_sections(math_data)
    assert len(sections) >= 1
    assert "Триггер:" in sections[0][1]
    assert "Боль" not in sections[0][1]


def test_story_humanize_channel_copy_overrides() -> None:
    text, trigger = hd_logic._story_humanize_channel_copy(
        "Суперсила сакральной самонаправленности",
        "старый триггер",
    )
    assert "кайфуешь" in text
    assert "чужие цели" in trigger


def test_story_wrap_lines_two_rows() -> None:
    lines = hd_logic._story_wrap_lines(
        "Абсолютная верность своему пути и деньги приходят когда делаешь то от чего кайфуешь сам",
        43,
        max_lines=2,
    )
    assert len(lines) <= 2
    assert all(len(line) <= 44 for line in lines)


def test_ensure_pdf_fonts_available_ok() -> None:
    if hd_logic.pdfmetrics is None:
        pytest.skip("reportlab not installed")
    hd_logic.ensure_pdf_fonts_available()


def test_load_static_block_reads_channels_index() -> None:
    hd_logic.load_static_block.cache_clear()
    block = hd_logic.load_static_block("channels", "20-34")
    assert isinstance(block, dict)
    assert block.get("title") or block.get("gift") or block.get("theme")


def test_story_channel_card_line_max_150_chars() -> None:
    line = hd_logic._story_channel_card_line("10-34")
    assert len(line) <= 150
    assert "кайфуешь" in line
    assert "Триггер:" in line
    assert "💼" not in line


def test_ensure_story_fonts_available_ok() -> None:
    hd_logic.ensure_story_fonts_available()


def test_ensure_story_fonts_available_raises_when_missing(tmp_path, monkeypatch) -> None:
    missing_dir = tmp_path / "fonts"
    missing_dir.mkdir()
    monkeypatch.setattr(hd_logic, "_STORY_FONT_DIR", missing_dir)
    monkeypatch.setattr(hd_logic, "_STORY_FONT_BOLD_PATH", missing_dir / "Roboto-Bold.ttf")
    monkeypatch.setattr(hd_logic, "_STORY_FONT_REGULAR_PATH", missing_dir / "Roboto-Regular.ttf")
    with pytest.raises(RuntimeError, match="HD story fonts missing"):
        hd_logic.ensure_story_fonts_available()


@pytest.mark.asyncio
async def test_hd_llm_semaphore_limits_parallel_calls(monkeypatch) -> None:
    import asyncio

    monkeypatch.setattr(hd_logic, "_HD_LLM_PARALLEL_LIMIT", 2)
    monkeypatch.setattr(hd_logic, "_HD_LLM_SEMAPHORE", None)
    sem = hd_logic._hd_llm_semaphore()
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal active, peak
        async with sem:
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.03)
            async with lock:
                active -= 1

    await asyncio.gather(*[worker() for _ in range(6)])
    assert peak <= 2


@pytest.mark.asyncio
async def test_premium_report_resilient_offline_when_llm_fails() -> None:
    existing = json.dumps({"schema_version": 3, "money": "Старый текст", "fast_facts": "x" * 50})
    with patch.object(
        hd_logic,
        "generate_premium_report",
        new=AsyncMock(side_effect=RuntimeError("hd_premium_unavailable")),
    ):
        report, llm_ok = await hd_logic.generate_premium_report_resilient(
            "Генератор",
            "18.08.1986 03:40 Чебоксары",
            existing_raw=existing,
            timeout_sec=5.0,
        )
    assert llm_ok is False
    assert report.get("money")
    assert report.get("static_reference") is not None or report.get("synthesis_meta", {}).get("upgrade_offline")


@pytest.mark.asyncio
async def test_premium_report_resilient_never_raises_on_empty_raw() -> None:
    with patch.object(
        hd_logic,
        "generate_premium_report",
        new=AsyncMock(side_effect=RuntimeError("all llm down")),
    ):
        report, llm_ok = await hd_logic.generate_premium_report_resilient(
            "Генератор",
            "18.08.1986 03:40 Чебоксары",
            existing_raw="",
            timeout_sec=1.0,
        )
    assert llm_ok is False
    assert report.get("plan")


@pytest.mark.asyncio
async def test_premium_report_resilient_offline_immediately_without_llm_keys() -> None:
    with (
        patch.object(hd_logic, "_openrouter_configured", return_value=False),
        patch.object(hd_logic, "_gemini_configured", return_value=False),
        patch.object(
            hd_logic,
            "generate_premium_report",
            new=AsyncMock(side_effect=AssertionError("LLM must not be called")),
        ),
    ):
        report, llm_ok = await hd_logic.generate_premium_report_resilient(
            "Генератор",
            "18.08.1986 03:40 Чебоксары",
            existing_raw="",
            timeout_sec=1.0,
        )
    assert llm_ok is False
    assert report.get("money")
    assert report.get("static_reference") is not None or report.get("synthesis_meta", {}).get(
        "upgrade_minimal"
    )
