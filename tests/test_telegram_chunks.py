"""Нарезка длинных ответов: без предварительной обрезки до 4090."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from platforms.telegram_chunks import answer_chat_text, split_telegram_text_chunks
from services.telegram_safe_text import prepare_telegram_html_text


def test_prepare_without_max_len_keeps_long_html() -> None:
    raw = ("<b>Блок</b>\n\n" + ("слово " * 200)) * 5
    out = prepare_telegram_html_text(raw, max_len=None)
    assert len(out) > 4090
    assert not out.endswith("…")


def test_prepare_default_still_truncates() -> None:
    raw = "x" * 5000
    out = prepare_telegram_html_text(raw)
    assert len(out) <= 4090
    assert out.endswith("…")


def test_split_prefers_paragraph_break() -> None:
    text = ("Абзац один. " * 40) + "\n\n" + ("Абзац два. " * 40)
    parts = split_telegram_text_chunks(text, 500)
    assert len(parts) >= 2
    assert all(len(p) <= 500 for p in parts)
    assert sum(len(p) for p in parts) == len(text)


@pytest.mark.asyncio
async def test_answer_chat_text_sends_multiple_parts_for_long_reply() -> None:
    long_body = ("Практический совет и пример. " * 80) + "\n\n"
    long_text = "".join(f"<b>{i}. Блок</b>\n{long_body}" for i in range(1, 6))
    assert len(prepare_telegram_html_text(long_text, max_len=None)) > 4090

    message = SimpleNamespace(answer=AsyncMock())
    settings = SimpleNamespace(
        chat_chunk_reply_threshold=3500,
        chat_reply_chunk_size=3900,
    )
    await answer_chat_text(message, long_text, settings)  # type: ignore[arg-type]
    assert message.answer.await_count >= 2
    sent_parts = [call.args[0] for call in message.answer.await_args_list]
    assert all(len(p) <= 4090 for p in sent_parts)
    # Полный ответ дошёл кусками, а не одним обрезанным сообщением с «…».
    assert sum(len(p) for p in sent_parts) > 4090
