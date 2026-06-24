"""Фоновая запись диалога: защита от NULL в dialog_messages.content."""

from __future__ import annotations

import json

import pytest

from services.dialog_write_worker import _normalize_assistant_text, commit_assistant_turn_queued
from services.table_xlsx_flow import _resolve_fast_path_assistant_text


def test_normalize_assistant_text_empty() -> None:
    assert _normalize_assistant_text(None).startswith("ℹ️")
    assert _normalize_assistant_text("").startswith("ℹ️")
    assert _normalize_assistant_text("  ").startswith("ℹ️")
    assert _normalize_assistant_text("ok") == "ok"


def test_resolve_fast_path_assistant_text_fallback() -> None:
    text = _resolve_fast_path_assistant_text(None, title="WB", rows=[])
    assert text
    data = json.loads(text)
    assert data["fast_path"] is True
    assert data["title"] == "WB"


def test_resolve_fast_path_assistant_text_from_rows() -> None:
    rows = [["Месяц", "Сумма"], ["Янв", "100"]]
    text = _resolve_fast_path_assistant_text(None, title="Отчёт", rows=rows)
    data = json.loads(text)
    assert data["headers"] == ["Месяц", "Сумма"]
    assert data["rows"] == [["Янв", "100"]]


@pytest.mark.asyncio
async def test_commit_assistant_turn_queued_never_persists_empty(repo_module) -> None:
    uid = 99001
    await repo_module.ensure_user(uid)
    await commit_assistant_turn_queued(uid, "", prune_keep=20)
    rows = await repo_module.dialog_fetch_last(uid, limit=5)
    assert rows
    assert rows[-1][0] == "assistant"
    assert rows[-1][1].strip()
