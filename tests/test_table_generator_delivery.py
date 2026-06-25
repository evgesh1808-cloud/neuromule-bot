"""Двухэтапная доставка table_generator: график отдельно, таблица + Excel."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from platforms.table_generator_delivery import (
    _CAPTION_SAFE_MAX,
    _chart_short_caption,
    send_table_generator_pack,
)


def test_chart_short_caption_under_limit() -> None:
    cap = _chart_short_caption("Продажи за Август")
    assert len(cap) < _CAPTION_SAFE_MAX
    assert "Август" in cap
    assert "Визуализация" in cap


@pytest.mark.asyncio
async def test_send_chart_then_document_separately() -> None:
    long_table = "<b>📊 ФИНАНСОВЫЙ ЭКСПРЕСС-АНАЛИЗ</b>\n" + ("A │ B\n" * 50)
    pack = SimpleNamespace(
        xlsx_bytes=b"PK\x03\x04fake",
        telegram_caption_html=long_table,
        chart_png_bytes=b"\x89PNG\r\n\x1a\n" + b"x" * 100,
        chart_type="bar",
        rows=[["A"], ["1"]],
    )
    message = MagicMock()
    message.from_user.id = 1
    message.chat.id = 1
    message.answer_photo = AsyncMock(return_value=SimpleNamespace(message_id=10))
    message.answer = AsyncMock()
    message.answer_document = AsyncMock()

    raw_json = json.dumps(
        {"title": "Продажи за Август", "headers": ["A"], "rows": [["1"]]},
        ensure_ascii=False,
    )

    with patch(
        "platforms.table_generator_delivery.build_table_generator_pack",
        return_value=pack,
    ), patch(
        "platforms.table_generator_delivery._cache_session",
        return_value=True,
    ), patch(
        "platforms.table_generator_delivery.answer_chat_text",
        new=AsyncMock(),
    ) as text_mock:
        ok = await send_table_generator_pack(
            message,
            raw_json,
            table_subrole="wb_ozon_finance",
        )

    assert ok is True
    message.answer_photo.assert_awaited_once()
    message.answer.assert_not_awaited()
    text_mock.assert_awaited_once()
    assert "ФИНАНСОВЫЙ" in text_mock.await_args.args[1]
    message.answer_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_short_table_in_document_caption() -> None:
    short_table = "<b>📊 Отчёт</b>\n<pre>A │ B</pre>"
    pack = SimpleNamespace(
        xlsx_bytes=b"PK",
        telegram_caption_html=short_table,
        chart_png_bytes=b"\x89PNG\r\n\x1a\n",
        chart_type="bar",
        rows=[["A"], ["1"]],
    )
    message = MagicMock()
    message.from_user.id = 1
    message.chat.id = 1
    message.answer_photo = AsyncMock(return_value=SimpleNamespace(message_id=11))
    message.answer = AsyncMock()
    message.answer_document = AsyncMock()

    raw_json = json.dumps({"title": "T", "headers": ["A"], "rows": [["1"]]})

    with patch(
        "platforms.table_generator_delivery.build_table_generator_pack",
        return_value=pack,
    ), patch(
        "platforms.table_generator_delivery._cache_session",
        return_value=True,
    ):
        await send_table_generator_pack(message, raw_json)

    doc_caption = message.answer_document.await_args.kwargs.get("caption", "")
    assert "NeuroMule.xlsx" in doc_caption
