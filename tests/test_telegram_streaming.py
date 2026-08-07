"""telegram_streaming — throttled edit 1.8s + OpenRouter SSE."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from platforms.telegram_streaming import (
    TELEGRAM_STREAM_EDIT_INTERVAL_SEC,
    create_telegram_streaming_handle,
    send_telegram_streaming_text,
)


def test_stream_edit_interval_is_1_8_sec() -> None:
    assert TELEGRAM_STREAM_EDIT_INTERVAL_SEC == 1.8


@pytest.mark.asyncio
async def test_create_streaming_handle_first_chunk_sends_answer() -> None:
    message = MagicMock()
    message.answer = AsyncMock(return_value=MagicMock(chat=MagicMock(id=1), message_id=10))
    bot = MagicMock()
    bot.edit_message_text = AsyncMock()

    handle = create_telegram_streaming_handle(message, bot)
    await handle.on_stream("Hello", done=False)

    message.answer.assert_awaited_once()
    bot.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_telegram_streaming_text_uses_stream_callback() -> None:
    message = MagicMock()
    message.chat.id = 1
    message.answer = AsyncMock(return_value=MagicMock(chat=MagicMock(id=1), message_id=5))
    bot = MagicMock()
    bot.send_chat_action = AsyncMock()
    bot.edit_message_text = AsyncMock()
    settings = MagicMock()
    settings.chat_max_context_tokens_est = 1000
    settings.chat_char_per_token_est = 3

    with (
        patch(
            "platforms.telegram_streaming.ask_ai_messages",
            new_callable=AsyncMock,
            return_value={"content": "Done", "prompt_tokens": 1, "completion_tokens": 2},
        ) as ask,
        patch(
            "platforms.telegram_streaming.bot_typing_indicator",
        ) as typing_cm,
    ):
        typing_cm.return_value.__aenter__ = AsyncMock(return_value=None)
        typing_cm.return_value.__aexit__ = AsyncMock(return_value=None)
        result, handle = await send_telegram_streaming_text(
            bot=bot,
            message=message,
            settings=settings,
            openrouter_messages=[{"role": "user", "content": "hi"}],
            models=["google/gemini-2.5-flash"],
        )

    assert result["content"] == "Done"
    assert ask.await_count == 1
    stream_cb = ask.await_args.kwargs.get("stream_callback")
    assert stream_cb is not None
    assert stream_cb.__func__ is handle.on_stream.__func__
    typing_cm.assert_called_once()
