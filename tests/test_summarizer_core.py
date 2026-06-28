"""Тесты ядра саммаризатора (без реальных вызовов OpenAI/сети)."""
from __future__ import annotations

import pytest

from core.summarizer import chunk_text, resolve_raw_text, summarize_text


@pytest.mark.asyncio
async def test_resolve_plain_text() -> None:
    text = "a" * 120
    raw, kind = await resolve_raw_text(text)
    assert kind == "plain"
    assert raw == text


@pytest.mark.asyncio
async def test_summarize_too_short() -> None:
    result = await summarize_text("короткий текст")
    assert not result.ok
    assert result.error_code == "too_short"


def test_chunk_text_splits() -> None:
    parts = chunk_text("x" * 9000, limit=4000)
    assert len(parts) == 3
    assert sum(len(p) for p in parts) == 9000
