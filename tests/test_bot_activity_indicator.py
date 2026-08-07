"""bot_typing_indicator — фоновый typing без утечки задач."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.bot_activity_indicator import bot_typing_indicator


@pytest.mark.asyncio
async def test_bot_typing_indicator_telegram_sends_and_cancels() -> None:
    bot = MagicMock()
    bot.send_chat_action = AsyncMock()
    seen: list[str] = []

    async def _track(*args, **kwargs):
        seen.append("typing")

    bot.send_chat_action.side_effect = _track

    async with bot_typing_indicator(bot, 42, platform="telegram", interval_sec=0.05):
        await asyncio.sleep(0.12)

    assert seen
    assert bot.send_chat_action.await_count >= 1


@pytest.mark.asyncio
async def test_bot_typing_indicator_vk_set_activity() -> None:
    api = MagicMock()
    api.messages.set_activity = AsyncMock()

    async with bot_typing_indicator(api, 100, platform="vk", interval_sec=0.05):
        await asyncio.sleep(0.02)

    api.messages.set_activity.assert_awaited()
    call_kw = api.messages.set_activity.await_args.kwargs
    assert call_kw["peer_id"] == 100
    assert call_kw["type"] == "typing"
