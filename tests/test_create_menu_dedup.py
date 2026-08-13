"""Дедупликация повторных нажатий «🎨 Создать»."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content import messages as msg


@pytest.mark.asyncio
async def test_send_create_menu_dedup_within_window() -> None:
    from platforms.handlers import menu_support

    message = MagicMock()
    message.from_user.id = 9001
    message.answer = AsyncMock()

    with patch.object(menu_support, "create_menu", return_value=MagicMock()):
        await menu_support.send_create_menu_screen(message)
        await menu_support.send_create_menu_screen(message)

    assert message.answer.await_count == 1


@pytest.mark.asyncio
async def test_send_create_menu_allowed_after_window(monkeypatch: pytest.MonkeyPatch) -> None:
    from platforms.handlers import menu_support

    monkeypatch.setattr(menu_support, "_CREATE_MENU_DEDUP_SEC", 0.0)

    message = MagicMock()
    message.from_user.id = 9002
    message.answer = AsyncMock()

    with patch.object(menu_support, "create_menu", return_value=MagicMock()):
        await menu_support.send_create_menu_screen(message)
        await menu_support.send_create_menu_screen(message)

    assert message.answer.await_count == 2
