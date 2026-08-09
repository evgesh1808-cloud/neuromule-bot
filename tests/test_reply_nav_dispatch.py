"""Regression: Reply nav fallback при VS16 и пропущенном handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content import messages as msg


@pytest.mark.asyncio
async def test_dispatch_reply_nav_button_tariffs_fe0f() -> None:
    from platforms.handlers.menu_support import dispatch_reply_nav_button

    message = MagicMock()
    message.text = "🚀️ Тарифы"
    message.from_user.id = 1
    state = AsyncMock()

    with patch(
        "platforms.handlers.menu_support.show_tariffs_from_short_menu",
        new_callable=AsyncMock,
    ) as show_tariffs:
        handled = await dispatch_reply_nav_button(message, state)
        assert handled is True
        show_tariffs.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_dispatch_unknown_nav_returns_false() -> None:
    from platforms.handlers.menu_support import dispatch_reply_nav_button

    message = MagicMock()
    message.text = "случайный текст"
    state = MagicMock()
    assert await dispatch_reply_nav_button(message, state) is False


@pytest.mark.asyncio
async def test_dispatch_tariffs_clears_fsm() -> None:
    from platforms.handlers.menu_support import dispatch_reply_nav_button

    message = MagicMock()
    message.text = "🚀 Тарифы"
    message.from_user.id = 1
    state = AsyncMock()

    with patch(
        "platforms.handlers.menu_support.show_tariffs_from_short_menu",
        new_callable=AsyncMock,
    ):
        assert await dispatch_reply_nav_button(message, state) is True
        state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_neurotext_does_not_clear_fsm() -> None:
    from platforms.handlers.menu_support import dispatch_reply_nav_button

    message = MagicMock()
    message.text = msg.BTN_REPLY_NEUROTEXT
    message.from_user.id = 1
    state = AsyncMock()

    with patch(
        "platforms.handlers.menu_support.reply_create_neurotext",
        new_callable=AsyncMock,
    ):
        assert await dispatch_reply_nav_button(message, state) is True
        state.clear.assert_not_awaited()


@pytest.mark.asyncio
async def test_reply_nav_middleware_intercepts_before_handler() -> None:
    from aiogram import types

    from platforms.telegram_middleware import ReplyNavDispatchMiddleware

    middleware = ReplyNavDispatchMiddleware()
    message = MagicMock(spec=types.Message)
    message.text = msg.BTN_TARIFFS
    state = AsyncMock()
    handler = AsyncMock(return_value="ok")

    with patch(
        "platforms.telegram_utils.try_dispatch_reply_nav_button",
        new_callable=AsyncMock,
        return_value=True,
    ) as dispatch:
        result = await middleware(handler, message, {"state": state})
        assert result is None
        dispatch.assert_awaited_once_with(message, state)
        handler.assert_not_awaited()
