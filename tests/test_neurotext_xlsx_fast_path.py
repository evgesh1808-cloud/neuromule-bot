"""xlsx/csv: табличный пайплайн только в роли table_generator (ИИ-Аналитик Excel)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import content.messages as msg
from platforms.neurotext_input import (
    _normalize_document_caption,
    handle_neurotext_user_message,
)
from services.use_cases.chat_turn import ChatTurnOutcome


def test_normalize_document_caption_strips_zero_width() -> None:
    message = SimpleNamespace(caption=" \u200b\u200c ")
    assert _normalize_document_caption(message) == ""


@pytest.mark.asyncio
async def test_standard_xlsx_does_not_use_table_pipeline() -> None:
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={"text_role": "standard"})
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    state.get_state = AsyncMock(return_value=None)

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
    message.answer = AsyncMock()

    with patch(
        "platforms.neurotext_input.run_xlsx_fast_path_turn",
        new=AsyncMock(),
    ) as fast_mock, patch(
        "platforms.neurotext_input.run_chat_turn",
        new=AsyncMock(),
    ) as chat_mock:
        await handle_neurotext_user_message(message, state)

    fast_mock.assert_not_awaited()
    chat_mock.assert_not_awaited()
    message.answer.assert_awaited_once()
    sent_text = message.answer.await_args.args[0]
    assert msg.BTN_TEXT_ROLE_TABLE in sent_text
    state.update_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_table_generator_bare_xlsx_uses_fast_path() -> None:
    from services.use_cases.chat_turn import ChatTurnResult

    state = AsyncMock()
    state.get_data = AsyncMock(
        return_value={"text_role": "table_generator", "table_subrole": "standard_report"}
    )
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    state.get_state = AsyncMock(return_value=None)

    message = MagicMock()
    message.from_user.id = 700002
    message.chat.id = 700002
    message.photo = None
    message.document = SimpleNamespace(
        file_id="xlsx2",
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
    worker = SimpleNamespace(rows=[["A"], ["1"]], calculated_total=0.0)

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
    call_kwargs = fast_mock.await_args.kwargs
    assert call_kwargs.get("table_subrole") != "wb_ozon_finance"
    assert call_kwargs.get("marketplace_platform") is None
