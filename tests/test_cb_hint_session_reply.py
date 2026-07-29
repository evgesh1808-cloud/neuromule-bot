"""Callback Suggested Reply: HintSession ``btn:`` + legacy soft-fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content import messages as msg
from services.standard_suggested_replies import (
    clear_suggested_replies_for_tests,
    create_hint_session,
    parse_hint_btn_callback,
)


@pytest.fixture(autouse=True)
def _clear_hint_cache() -> None:
    clear_suggested_replies_for_tests()
    yield
    clear_suggested_replies_for_tests()


def test_hint_user_turn_includes_root_and_expanded_label() -> None:
    from platforms.handlers.generation_fsm import _hint_user_turn

    turn = _hint_user_turn(
        root_user_prompt="Как начать тхэквондо?",
        label="Про разминку?",
    )
    assert "По теме «Как начать тхэквондо?»:" in turn
    assert "разминк" in turn.lower()


@pytest.mark.asyncio
async def test_cb_btn_hint_session_strips_keyboard_and_passes_focused_anchor() -> None:
    from platforms.handlers.generation_fsm import cb_standard_suggested_reply

    body = (
        "1. Разминка: суставная гимнастика 10 минут.\n"
        "2. Стойки: базовая позиция ног.\n"
        "3. Удар ногои: контроль высоты."
    )
    labels = ["Про разминку?", "Про стойки?", "Про удар?"]
    action_uuid = create_hint_session(
        42,
        body=body,
        labels=labels,
        root_user_prompt="Как начать тхэквондо?",
        message_id=500,
    )
    callback_data = f"{msg.CB_HINT_BTN_PREFIX}0:{action_uuid}"
    assert parse_hint_btn_callback(callback_data) == (0, action_uuid)

    callback = MagicMock()
    callback.data = callback_data
    callback.from_user.id = 42
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.edit_reply_markup = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.message.html_text = "устаревший html — не должен стать якорем"
    state = MagicMock()
    state.update_data = AsyncMock()

    with (
        patch(
            "services.god_mode.billing_bypass",
            return_value=True,
        ),
        patch(
            "platforms.neurotext_flow.ensure_neurotext_waiting_state",
            new_callable=AsyncMock,
        ),
        patch(
            "platforms.neurotext_input.handle_neurotext_user_message",
            new_callable=AsyncMock,
        ) as handle,
    ):
        await cb_standard_suggested_reply(callback, state)

    callback.answer.assert_awaited()
    # Без show_alert на успешном пути
    assert callback.answer.await_args.kwargs.get("show_alert") is not True
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    handle.assert_awaited_once()
    kwargs = handle.await_args.kwargs
    assert kwargs["forced_user_id"] == 42
    user_turn = kwargs["forced_user_text"]
    assert "Как начать тхэквондо" in user_turn
    assert "разминк" in user_turn.lower()
    anchor = kwargs["anchor_assistant_text"] or ""
    assert "Разминка" in anchor or "разминк" in anchor.lower()
    assert "устаревший html" not in anchor


@pytest.mark.asyncio
async def test_cb_btn_missing_session_shows_stale_alert() -> None:
    from platforms.handlers.generation_fsm import cb_standard_suggested_reply

    callback = MagicMock()
    callback.data = f"{msg.CB_HINT_BTN_PREFIX}0:deadbeefdeadbeef"
    callback.from_user.id = 7
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.edit_reply_markup = AsyncMock()
    state = MagicMock()

    with patch(
        "platforms.neurotext_input.handle_neurotext_user_message",
        new_callable=AsyncMock,
    ) as handle:
        await cb_standard_suggested_reply(callback, state)

    handle.assert_not_awaited()
    callback.message.edit_reply_markup.assert_not_awaited()
    assert callback.answer.await_args.kwargs.get("show_alert") is True
    assert "устарела" in (callback.answer.await_args.args[0] or "").lower()


@pytest.mark.asyncio
async def test_cb_legacy_chat_hint_still_works() -> None:
    from platforms.handlers.generation_fsm import cb_standard_suggested_reply

    callback = MagicMock()
    callback.data = f"{msg.CB_CHAT_HINT_PREFIX}Про сроки?"
    callback.from_user.id = 3
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.edit_reply_markup = AsyncMock()
    callback.message.html_text = "Ответ про дедлайны проекта."
    callback.message.text = None
    callback.message.caption = None
    state = MagicMock()
    state.update_data = AsyncMock()

    with (
        patch("services.god_mode.billing_bypass", return_value=True),
        patch(
            "platforms.neurotext_flow.ensure_neurotext_waiting_state",
            new_callable=AsyncMock,
        ),
        patch(
            "platforms.neurotext_input.handle_neurotext_user_message",
            new_callable=AsyncMock,
        ) as handle,
    ):
        await cb_standard_suggested_reply(callback, state)

    handle.assert_awaited_once()
    kwargs = handle.await_args.kwargs
    assert "срок" in (kwargs["forced_user_text"] or "").lower() or "практик" in (
        kwargs["forced_user_text"] or ""
    ).lower()
    assert "дедлайн" in (kwargs["anchor_assistant_text"] or "").lower()
