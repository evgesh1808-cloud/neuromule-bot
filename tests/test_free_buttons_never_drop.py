"""Regression: idle FREE chat must always attach hint buttons."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import InlineKeyboardMarkup

from content import messages as msg
from services.billing.types import TariffTier
from services.use_cases.chat_turn import ChatTurnOutcome, ChatTurnResult


@pytest.mark.asyncio
async def test_success_reply_keyboard_always_has_free_hints() -> None:
    from platforms.neurotext_input import _success_reply_keyboard

    result = ChatTurnResult(
        outcome=ChatTurnOutcome.SUCCESS,
        assistant_message="В Люберцах есть секции футбола для детей.",
        effective_text_role="standard",
        suggested_replies=(),
        tariff=TariffTier.FREE,
    )
    kb, blogger_id = await _success_reply_keyboard(42, result)
    assert blogger_id is None
    assert isinstance(kb, InlineKeyboardMarkup)
    flat = [b for row in kb.inline_keyboard for b in row]
    assert len(flat) == 3
    assert all((b.callback_data or "").startswith(msg.CB_CHAT_HINT_PREFIX) for b in flat)


@pytest.mark.asyncio
async def test_iron_free_keyboard_when_markup_helpers_fail() -> None:
    from platforms.neurotext_input import _iron_free_hint_keyboard

    result = ChatTurnResult(
        outcome=ChatTurnOutcome.SUCCESS,
        assistant_message="Ромашковый чай можно пить.",
        effective_text_role="standard",
        tariff=TariffTier.FREE,
    )
    kb = _iron_free_hint_keyboard(result)
    assert kb is not None
    assert len(kb.inline_keyboard) == 3


@pytest.mark.asyncio
async def test_chat_handler_delegates_to_neurotext() -> None:
    from platforms.handlers import payment_misc

    message = MagicMock()
    message.text = "Куда на футбол"
    message.from_user.id = 7
    state = MagicMock()

    with (
        patch(
            "platforms.handlers.payment_misc.has_neurotext_message_input",
            return_value=True,
        ),
        patch(
            "platforms.handlers.payment_misc._reply_menu_button_texts",
            return_value=frozenset(),
        ),
        patch(
            "platforms.handlers.payment_misc.is_reply_to_bot_message",
            return_value=False,
        ),
        patch(
            "platforms.neurotext_input.handle_neurotext_user_message",
            new_callable=AsyncMock,
        ) as handle,
    ):
        await payment_misc.chat_handler(message, state)
        handle.assert_awaited_once_with(message, state)
