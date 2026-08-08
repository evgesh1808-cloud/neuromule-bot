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
    state = MagicMock()

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
