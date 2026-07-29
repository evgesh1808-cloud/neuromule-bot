"""Atomic FREE Standard send: text never lands without 3 chat_hint buttons."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

from content import messages as msg
from config import settings


def _bad_request() -> TelegramBadRequest:
    return TelegramBadRequest(method=MagicMock(), message="Bad Request: can't parse entities")


@pytest.mark.asyncio
async def test_answer_free_standard_success_always_sends_markup() -> None:
    from platforms.telegram_chunks import answer_free_standard_success
    from services.standard_suggested_replies import (
        clear_suggested_replies_for_tests,
        get_hint_session,
    )

    clear_suggested_replies_for_tests()
    message = MagicMock()
    message.chat.id = 1
    message.from_user.id = 42
    sent = MagicMock()
    sent.message_id = 777
    message.answer = AsyncMock(return_value=sent)

    out = await answer_free_standard_success(
        message,
        "Короткий ответ про кота.",
        settings,
        user_id=42,
        labels=["Грустнее?", "Короче?", "Другой стиль?"],
        root_user_prompt="Напиши про кота",
    )
    assert out is sent
    assert message.answer.await_count == 1
    kwargs = message.answer.await_args.kwargs
    assert isinstance(kwargs.get("reply_markup"), InlineKeyboardMarkup)
    kb = kwargs["reply_markup"]
    flat = [b for row in kb.inline_keyboard for b in row]
    assert len(flat) == 3
    assert all((b.callback_data or "").startswith(msg.CB_HINT_BTN_PREFIX) for b in flat)
    assert all(len(b.callback_data or "") <= 64 for b in flat)
    # bind после send
    uuid = (flat[0].callback_data or "").rsplit(":", 1)[-1]
    session = get_hint_session(uuid, user_id=42)
    assert session is not None
    assert session.message_id == 777
    assert session.root_user_prompt == "Напиши про кота"
    assert "кота" in session.body.lower() or "кот" in session.body.lower()
    clear_suggested_replies_for_tests()


@pytest.mark.asyncio
async def test_answer_free_standard_success_never_sends_bare_text() -> None:
    """HTML+kb fails, plain+kb fails → ASCII emergency still WITH markup."""
    from platforms.telegram_chunks import answer_free_standard_success
    from services.standard_suggested_replies import clear_suggested_replies_for_tests

    clear_suggested_replies_for_tests()
    message = MagicMock()
    message.chat.id = 2
    message.from_user.id = 9
    sent = MagicMock()
    sent.message_id = 12
    calls = {"n": 0}

    async def _answer(*args, **kwargs):
        calls["n"] += 1
        markup = kwargs.get("reply_markup")
        assert markup is not None
        if calls["n"] < 3:
            raise _bad_request()
        return sent

    message.answer = AsyncMock(side_effect=_answer)

    out = await answer_free_standard_success(
        message,
        "<b>Стих</b> про кота",
        settings,
        user_id=9,
        root_user_prompt="Стих",
    )
    assert out is sent
    assert calls["n"] == 3
    assert message.answer.await_count == 3
    for call in message.answer.await_args_list:
        assert call.kwargs.get("reply_markup") is not None
        kb = call.kwargs["reply_markup"]
        assert len(kb.inline_keyboard) == 3
    clear_suggested_replies_for_tests()


@pytest.mark.asyncio
async def test_answer_chat_text_does_not_commit_bare_when_markup_required() -> None:
    from platforms.telegram_chunks import answer_chat_text
    from services.standard_suggested_replies import build_free_hint_keyboard

    message = MagicMock()
    message.answer = AsyncMock(side_effect=_bad_request())
    kb = build_free_hint_keyboard()

    with pytest.raises(TelegramBadRequest):
        await answer_chat_text(
            message,
            "Текст",
            settings,
            reply_markup=kb,
        )
    # Не должно быть успешного answer без markup.
    for call in message.answer.await_args_list:
        assert call.kwargs.get("reply_markup") is kb or call.kwargs.get("reply_markup") is not None
