"""Bare .xlsx без caption всегда идёт в локальный fast-path."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from platforms.neurotext_input import (
    _is_bare_xlsx_document,
    _normalize_document_caption,
    handle_neurotext_user_message,
)
from services.use_cases.chat_turn import ChatTurnOutcome


def test_normalize_document_caption_strips_zero_width() -> None:
    msg = SimpleNamespace(caption=" \u200b\u200c ")
    assert _normalize_document_caption(msg) == ""


def test_is_bare_xlsx_document() -> None:
    bare = SimpleNamespace(
        document=SimpleNamespace(file_name="report.xlsx"),
        caption=None,
    )
    with_caption = SimpleNamespace(
        document=SimpleNamespace(file_name="report.xlsx"),
        caption="Сводка",
    )
    assert _is_bare_xlsx_document(bare) is True
    assert _is_bare_xlsx_document(with_caption) is False


@pytest.mark.asyncio
async def test_bare_xlsx_uses_fast_path_when_fsm_role_is_standard() -> None:
    from services.use_cases.chat_turn import ChatTurnResult

    state = AsyncMock()
    state.get_data = AsyncMock(return_value={"text_role": "standard"})
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()

    message = MagicMock()
    message.from_user.id = 700001
    message.chat.id = 700001
    message.photo = None
    message.document = SimpleNamespace(
        file_id="xlsx1",
        file_name="sales.xlsx",
        file_size=100,
    )
    message.caption = None
    fast_result = ChatTurnResult(
        outcome=ChatTurnOutcome.SUCCESS,
        effective_text_role="table_generator",
        table_raw_json='{"title":"T","headers":["A"],"rows":[["1"]]}',
        table_report_id=1,
    )

    worker = SimpleNamespace(rows=[["A"], ["1"]])

    with patch(
        "platforms.neurotext_input.download_telegram_document_to_path",
        new=AsyncMock(return_value="/tmp/fake-sales.xlsx"),
    ), patch(
        "platforms.neurotext_input.run_table_processing_worker_async",
        new=AsyncMock(return_value=worker),
    ), patch(
        "platforms.neurotext_input.run_xlsx_fast_path_turn",
        new=AsyncMock(return_value=fast_result),
    ) as fast_mock, patch(
        "platforms.neurotext_input.run_chat_turn",
        new=AsyncMock(),
    ) as chat_mock, patch(
        "platforms.neurotext_input.send_table_generator_pack",
        new=AsyncMock(return_value=True),
    ), patch(
        "platforms.neurotext_input.deps.bot",
        return_value=MagicMock(),
    ), patch(
        "platforms.neurotext_input._table_xlsx_allowed",
        new=AsyncMock(return_value=True),
    ), patch(
        "platforms.neurotext_input.flood_safe_answer",
        new=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())),
    ), patch(
        "platforms.neurotext_input.flood_safe_chat_action_loop",
    ) as action_ctx:
        action_ctx.return_value.__aenter__ = AsyncMock(return_value=None)
        action_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
        await handle_neurotext_user_message(message, state)

    fast_mock.assert_awaited_once()
    chat_mock.assert_not_awaited()
    state.update_data.assert_any_await(text_role="table_generator")
    call_kwargs = fast_mock.await_args.kwargs
    assert call_kwargs.get("table_subrole") != "wb_ozon_finance"
    assert call_kwargs.get("marketplace_platform") is None
