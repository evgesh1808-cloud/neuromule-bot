"""
Live-отображение ответа бота: первое сообщение + редкие ``edit_message_text`` по накопленному тексту из SSE.

Интервал между правками берётся из конфига (по умолчанию ~0.8 с), чтобы не упереться в лимиты Telegram.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from config import Settings
from services.telegram_safe_text import prepare_telegram_html_text, sanitize_telegram_plain_text

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

logger = logging.getLogger(__name__)


@dataclass
class StreamReplyHandle:
    """
    Колбэк SSE для ``ask_ai_messages`` и принудительная финальная правка полным текстом.

    Передавайте ``handle.on_stream`` в ``stream_callback``; после ``run_chat_turn`` вызовите
    ``await handle.finalize(assistant_message)``.
    """

    _apply_text: Any = field(repr=False)

    async def on_stream(self, full_text: str, done: bool) -> None:
        capped = prepare_telegram_html_text(full_text or "")
        await self._apply_text(capped, force=done)

    async def finalize(self, full_text: str) -> None:
        capped = prepare_telegram_html_text(full_text or "")
        await self._apply_text(capped, force=True)


def create_throttled_stream_reply(message: "Message", bot: "Bot", settings: Settings) -> StreamReplyHandle:
    """
    Фабрика live-ответа в чате.

    При первом чанке создаётся новое сообщение; далее текст обновляется не чаще
    ``telegram_stream_edit_interval_sec`` (и на финальном чанке SSE).
    """

    state: dict = {
        "sent_msg": None,
        "last_edit_mono": 0.0,
        "last_text": "",
        "use_html": True,
    }
    interval = max(0.15, float(settings.telegram_stream_edit_interval_sec))

    async def _send(text: str):
        if state["use_html"]:
            try:
                return await message.answer(text, parse_mode=ParseMode.HTML)
            except TelegramBadRequest:
                state["use_html"] = False
        return await message.answer(sanitize_telegram_plain_text(text))

    async def _edit(text: str) -> bool:
        if state["sent_msg"] is None:
            return False
        payload = text if text else "…"
        if state["use_html"]:
            try:
                await bot.edit_message_text(
                    chat_id=state["sent_msg"].chat.id,
                    message_id=state["sent_msg"].message_id,
                    text=payload,
                    parse_mode=ParseMode.HTML,
                )
                return True
            except TelegramBadRequest:
                state["use_html"] = False
        try:
            await bot.edit_message_text(
                chat_id=state["sent_msg"].chat.id,
                message_id=state["sent_msg"].message_id,
                text=sanitize_telegram_plain_text(payload),
            )
            return True
        except TelegramBadRequest:
            return False

    async def _apply_text(capped: str, *, force: bool = False) -> None:
        if not capped:
            return
        now = time.monotonic()
        if state["sent_msg"] is None:
            state["sent_msg"] = await _send(capped)
            state["last_edit_mono"] = now
            state["last_text"] = capped
            return
        if capped == state["last_text"]:
            return
        elapsed = now - state["last_edit_mono"]
        if not force and elapsed < interval:
            return
        if await _edit(capped):
            state["last_text"] = capped
            state["last_edit_mono"] = now
        elif force:
            logger.warning(
                "stream reply finalize edit failed chat_id=%s message_id=%s len=%s",
                state["sent_msg"].chat.id,
                state["sent_msg"].message_id,
                len(capped),
            )

    return StreamReplyHandle(_apply_text=_apply_text)
