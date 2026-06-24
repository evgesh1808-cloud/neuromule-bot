"""Callback wb_chart: переключение типов графика."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content import messages as msg
from platforms.handlers.table_chart_cb import _parse_wb_chart_callback, switch_wb_chart


def test_parse_wb_chart_callback() -> None:
    assert _parse_wb_chart_callback("wb_chart:barh:42") == ("barh", 42)
    assert _parse_wb_chart_callback("wb_chart:line:7") == ("line", 7)
    assert _parse_wb_chart_callback("wb_chart:pie:0") is None
    assert _parse_wb_chart_callback("tbl_chart:bar") is None


@pytest.mark.asyncio
async def test_switch_wb_chart_edits_media() -> None:
    callback = MagicMock()
    callback.from_user.id = 100
    callback.data = f"{msg.CB_WB_CHART_PREFIX}pie:5"
    callback.message = MagicMock()
    callback.message.caption = "📊 Короткая подпись"
    callback.message.html_caption = None
    callback.message.edit_media = AsyncMock()
    callback.answer = AsyncMock()

    rows = [["Предмет", "К перечислению за товар, руб."], ["A", "10"], ["B", "20"]]

    with patch(
        "platforms.handlers.table_chart_cb.fetch_table_report_rows_for_user",
        new=AsyncMock(return_value=(rows, "T")),
    ), patch(
        "platforms.handlers.table_chart_cb.render_wb_chart_from_rows",
        return_value=b"\x89PNG\r\n\x1a\n" + b"x" * 50,
    ), patch(
        "platforms.handlers.table_chart_cb.update_active_chart",
    ):
        await switch_wb_chart(callback)

    callback.message.edit_media.assert_awaited_once()
    callback.answer.assert_awaited_once()
