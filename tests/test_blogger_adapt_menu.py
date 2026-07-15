"""Тесты кнопки «🔄 Адаптировать» (подменю площадок)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content import messages as msg
from services import blogger_post_cache


@pytest.mark.asyncio
async def test_cb_blogger_adapt_menu_opens_platform_keyboard() -> None:
    from platforms.blogger_flow import cb_blogger_adapt_menu

    sample = """===ТЕЛО ПОСТА===
Тестовое тело поста для адаптации."""
    post_id = blogger_post_cache.remember(801, sample)
    await blogger_post_cache.bind_telegram_message(post_id, 801, chat_id=3, message_id=40)

    callback = MagicMock()
    callback.from_user.id = 801
    callback.data = f"{msg.CB_BLOG_ADAPT_PREFIX}{post_id}"
    callback.message.chat.id = 3
    callback.message.message_id = 40
    callback.message.edit_reply_markup = AsyncMock()
    callback.answer = AsyncMock()

    await cb_blogger_adapt_menu(callback)

    callback.message.edit_reply_markup.assert_awaited_once()
    callback.answer.assert_awaited_once_with("Выберите площадку 👇")
