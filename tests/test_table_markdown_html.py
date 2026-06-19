"""Конвертация Markdown-таблиц роли table_generator для Telegram HTML."""

from __future__ import annotations

from services.telegram_safe_text import markdown_tables_to_telegram_html, prepare_telegram_html_text
from services.use_cases.chat_turn import format_assistant_for_role


def test_markdown_table_converts_to_pre_block() -> None:
    raw = (
        "| Имя | Возраст |\n"
        "|---|---|\n"
        "| Анна | 28 |\n"
        "| Борис | 31 |"
    )
    out = markdown_tables_to_telegram_html(raw)
    assert out.startswith("<pre>")
    assert out.endswith("</pre>")
    assert "|" not in out
    assert "Имя" in out and "Анна" in out and "28" in out
    assert "─" in out


def test_format_assistant_for_role_table_generator() -> None:
    raw = "| A | B |\n|---|---|\n| 1 | 2 |"
    out = format_assistant_for_role(raw, "table_generator")
    assert "<pre>" in out
    assert "1" in out and "2" in out


def test_prepare_telegram_html_preserves_pre_table() -> None:
    table = markdown_tables_to_telegram_html("| X | Y |\n|---|---|\n| 1 | 2 |")
    prepared = prepare_telegram_html_text(table)
    assert "<pre>" in prepared
    assert "│" in prepared


def test_non_table_role_unchanged_pipe_behavior() -> None:
    raw = "| not | a table row alone |"
    out = format_assistant_for_role(raw, "standard")
    assert "<pre>" not in out
