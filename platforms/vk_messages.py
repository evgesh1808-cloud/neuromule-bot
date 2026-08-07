"""Отправка plain-text сообщений в VK (без Telegram HTML)."""

from __future__ import annotations

import random
from typing import Any

from services.vk_api_retry import vk_api_call_with_retry
from services.vk_plain_text import vk_plain_text


async def vk_answer(message: Any, text: str) -> None:
    """``message.answer`` с очисткой Telegram-разметки."""
    await message.answer(vk_plain_text(text))


async def vk_send_message(bot: Any, peer_id: int, text: str) -> None:
    """``messages.send`` с retry и уникальным ``random_id``."""
    safe = vk_plain_text(text)

    async def _call() -> Any:
        return await bot.api.messages.send(
            peer_id=peer_id,
            message=safe,
            random_id=random.randint(1, 2_000_000_000),
        )

    await vk_api_call_with_retry(_call, context="messages.send")
