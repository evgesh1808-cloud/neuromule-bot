"""
Live-отображение ответа бота: первое сообщение + редкие ``edit_message_text`` по накопленному тексту из SSE.

Интервал между правками берётся из конфига (по умолчанию ~0.8 с), чтобы не упереться в лимиты Telegram.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramBadRequest

from config import Settings
from services.ai_text import StreamCallback

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message


def create_throttled_stream_reply(message: "Message", bot: "Bot", settings: Settings) -> StreamCallback:
    """
    Фабрика колбэка для ``ask_ai_messages(..., stream_callback=...)``.

    При первом чанке создаётся новое сообщение в чате; далее текст обновляется не чаще,
    чем раз в ``telegram_stream_edit_interval_sec`` секунд (и обязательно на финальном чанке).
    """

    state: dict = {"sent_msg": None, "last_edit_mono": 0.0, "last_text": ""}
    interval = max(0.15, float(settings.telegram_stream_edit_interval_sec))

    async def on_stream(full_text: str, done: bool) -> None:
        """Вызывается из слоя AI на каждую дельту и один раз в конце (``done=True``)."""
        capped = (full_text or "")[:4090]
        now = time.monotonic()
        if state["sent_msg"] is None:
            if not capped:
                return
            state["sent_msg"] = await message.answer(capped)
            state["last_edit_mono"] = now
            state["last_text"] = capped
            return

        elapsed = now - state["last_edit_mono"]
        should_edit = done or elapsed >= interval
        if should_edit and capped != state["last_text"]:
            try:
                await bot.edit_message_text(
                    chat_id=state["sent_msg"].chat.id,
                    message_id=state["sent_msg"].message_id,
                    text=capped if capped else "…",
                )
                state["last_text"] = capped
                state["last_edit_mono"] = now
            except TelegramBadRequest:
                pass

    return on_stream
