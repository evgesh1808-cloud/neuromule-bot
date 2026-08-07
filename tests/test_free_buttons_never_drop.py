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
    from services.standard_suggested_replies import clear_suggested_replies_for_tests

    clear_suggested_replies_for_tests()
    result = ChatTurnResult(
        outcome=ChatTurnOutcome.SUCCESS,
        assistant_message="В Люберцах есть секции футбола для детей.",
        effective_text_role="standard",
        suggested_replies=(),
        tariff=TariffTier.FREE,
        root_user_prompt="Куда на футбол в Люберцах?",
    )
    kb, blogger_id, action_uuid = await _success_reply_keyboard(42, result)
    assert blogger_id is None
    assert action_uuid
    assert isinstance(kb, InlineKeyboardMarkup)
    flat = [b for row in kb.inline_keyboard for b in row]
    assert len(flat) == 3
    assert all((b.callback_data or "").startswith(msg.CB_HINT_BTN_PREFIX) for b in flat)
    assert all(len(b.callback_data or "") <= 64 for b in flat)
    clear_suggested_replies_for_tests()


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
            "platforms.telegram_utils.is_image_reply_button_text",
            return_value=False,
        ),
        patch(
            "platforms.image_menu_flow.can_intercept_text_as_image_prompt",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "platforms.handlers.payment_misc.is_reply_to_bot_message",
            return_value=False,
        ),
        patch(
            "services.agent_intent_dispatch.try_agent_image_intent_telegram",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "platforms.neurotext_input.handle_neurotext_user_message",
            new_callable=AsyncMock,
        ) as handle,
    ):
        await payment_misc.chat_handler(message, state)
        handle.assert_awaited_once_with(message, state)
