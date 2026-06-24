"""Fast-path Excel, компактный JSON-промпт и DeepSeek fallback."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.table_xlsx_flow import (
    build_xlsx_api_user_prompt,
    rows_to_canonical_table_json,
    run_table_json_deepseek_fallback,
    run_xlsx_fast_path_turn,
)
from services.use_cases.chat_turn import ChatTurnOutcome


def test_rows_to_canonical_table_json() -> None:
    rows = [["Год", "Выручка"], ["2024", "100"], ["2025", "120"]]
    raw = rows_to_canonical_table_json(rows, title="Отчёт")
    assert raw is not None
    data = json.loads(raw)
    assert data["title"] == "Отчёт"
    assert data["headers"] == ["Год", "Выручка"]
    assert data["rows"] == [["2024", "100"], ["2025", "120"]]


def test_build_xlsx_api_user_prompt_truncates_preview() -> None:
    rows = [["A", "B"]] + [[str(i), str(i * 2)] for i in range(50)]
    prompt = build_xlsx_api_user_prompt("Сделай сводку", rows, title="T", max_preview_rows=5)
    assert "Сделай сводку" in prompt
    assert '"truncated": true' in prompt
    assert "Данные Excel (JSON):" in prompt


@pytest.mark.asyncio
async def test_run_xlsx_fast_path_turn_charges_and_returns_table(repo_module) -> None:
    from config import Settings
    from tests.conftest import TEST_ADMIN_IDS

    uid = TEST_ADMIN_IDS[0]
    await repo_module.ensure_user(uid)
    rows = [["Месяц", "Сумма"], ["Янв", "10"], ["Фев", "20"]]
    fake_plan = SimpleNamespace(
        blocked=False,
        energy_cost=20,
        crystal_cost=0,
    )
    billing_result = SimpleNamespace(
        plan=fake_plan,
        charge_id="c1",
        effective_role_id="table_generator",
        notice=None,
    )

    with patch(
        "services.table_xlsx_flow.allow_request",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.table_xlsx_flow.billing.resolve_and_charge_text_chat",
        new=AsyncMock(return_value=billing_result),
    ), patch(
        "services.table_xlsx_flow.dialog_append",
        new=AsyncMock(),
    ) as append_mock, patch(
        "services.table_xlsx_flow.commit_assistant_turn_queued",
        new=AsyncMock(),
    ), patch(
        "services.table_xlsx_flow.insert_table_report",
        new=AsyncMock(return_value=42),
    ), patch(
        "services.table_xlsx_flow.conv.schedule_memory_refresh",
    ):
        result = await run_xlsx_fast_path_turn(
            Settings(tg_token="x", openrouter_key="y", gemini_api_key="z"),
            uid,
            rows,
            file_name="sales.xlsx",
            title="sales",
        )

    assert result.outcome is ChatTurnOutcome.SUCCESS
    assert result.table_raw_json is not None
    assert result.table_report_id == 42
    append_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_table_json_deepseek_fallback_no_billing(repo_module) -> None:
    from config import Settings
    from tests.conftest import TEST_ADMIN_IDS

    uid = TEST_ADMIN_IDS[0]
    await repo_module.ensure_user(uid)
    sample_json = json.dumps(
        {"title": "T", "headers": ["A"], "rows": [["1"]]},
        ensure_ascii=False,
    )

    with patch(
        "services.table_xlsx_flow.ask_ai_messages",
        new=AsyncMock(
            return_value={
                "content": sample_json,
                "prompt_tokens": 10,
                "completion_tokens": 5,
            }
        ),
    ) as ask_mock, patch(
        "services.table_xlsx_flow.dialog_append",
        new=AsyncMock(),
    ), patch(
        "services.table_xlsx_flow.commit_assistant_turn_queued",
        new=AsyncMock(),
    ), patch(
        "services.table_xlsx_flow.insert_table_report",
        new=AsyncMock(return_value=7),
    ), patch(
        "services.table_xlsx_flow.get_persistent_memory",
        new=AsyncMock(return_value=""),
    ), patch(
        "services.table_xlsx_flow.conv.schedule_memory_refresh",
    ):
        result = await run_table_json_deepseek_fallback(
            Settings(tg_token="x", openrouter_key="y", gemini_api_key="z"),
            uid,
            '{"headers":["A"],"rows":[["1"]]}',
            dialog_user_text="[📊 Excel f.xlsx]",
        )

    assert result.outcome is ChatTurnOutcome.SUCCESS
    assert ask_mock.await_args.kwargs.get("temperature") == 0.1
    assert ask_mock.await_args.kwargs.get("text_role") == "table_generator"
