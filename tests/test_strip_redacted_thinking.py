"""Очистка <think> в ответах нейротекста."""

from services.use_cases.chat_turn import clean_markdown_to_html, strip_redacted_thinking


def test_strip_redacted_thinking_closed_block() -> None:
    raw = "Ответ.<think>скрыто</think> Конец."
    assert strip_redacted_thinking(raw) == "Ответ. Конец."


def test_strip_redacted_thinking_unclosed_tail() -> None:
    raw = "Видимый текст<think>незакрытый хвост"
    assert strip_redacted_thinking(raw) == "Видимый текст"


def test_strip_redacted_thinking_empty() -> None:
    assert strip_redacted_thinking("") == ""


def test_clean_markdown_to_html_strips_thinking_first() -> None:
    raw = "<think>x</think>**жирный**"
    out = clean_markdown_to_html(raw)
    assert "redacted_thinking" not in out
    assert "<b>жирный</b>" in out
