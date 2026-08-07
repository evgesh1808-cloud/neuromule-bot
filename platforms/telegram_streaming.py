"""
Live-текст в Telegram: OpenRouter SSE + throttled ``edit_message_text``.

Интервал правок фиксирован на 1.8 с — защита от rate-limit Telegram API.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from config import Settings
from services.ai_text import ChatCompletionResult, StreamCallback, ask_ai_messages
from services.bot_activity_indicator import bot_typing_indicator
from services.telegram_safe_text import prepare_telegram_html_text, sanitize_telegram_plain_text

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)

TELEGRAM_STREAM_EDIT_INTERVAL_SEC = 1.8


@dataclass
class TelegramStreamingHandle:
    """
    Колбэк для ``ask_ai_messages`` + финальная правка полным текстом и кнопками.

    Передавайте ``handle.on_stream`` в ``stream_callback``; после ``run_chat_turn``
    вызовите ``await handle.finalize(assistant_message, reply_markup=...)``.
    """

    _apply_text: Any = field(repr=False)
    _sent_msg_ref: Any = field(repr=False, default=lambda: None)

    async def on_stream(self, full_text: str, done: bool) -> None:
        capped = prepare_telegram_html_text(full_text or "")
        await self._apply_text(capped, force=done)

    async def finalize(
        self,
        full_text: str,
        *,
        reply_markup: "InlineKeyboardMarkup | None" = None,
        on_finalized: Any = None,
    ) -> "Message | None":
        capped = prepare_telegram_html_text(full_text or "")
        if not capped and reply_markup is not None:
            capped = "…"
        await self._apply_text(capped, force=True, reply_markup=reply_markup)
        sent = self._sent_message()
        if on_finalized is not None and sent is not None:
            await on_finalized(sent)
        return sent

    def _sent_message(self) -> "Message | None":
        return getattr(self, "_sent_msg_ref", lambda: None)()


def create_telegram_streaming_handle(
    message: "Message",
    bot: "Bot",
    *,
    edit_interval_sec: float = TELEGRAM_STREAM_EDIT_INTERVAL_SEC,
) -> TelegramStreamingHandle:
    """Фабрика live-ответа: первый чанк → ``answer``, далее ``edit`` не чаще ``edit_interval_sec``."""
    state: dict[str, Any] = {
        "sent_msg": None,
        "last_edit_mono": 0.0,
        "last_text": "",
        "use_html": True,
    }
    interval = max(TELEGRAM_STREAM_EDIT_INTERVAL_SEC, float(edit_interval_sec))

    async def _send(
        text: str,
        *,
        reply_markup: "InlineKeyboardMarkup | None" = None,
    ):
        kwargs: dict[str, Any] = {}
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        if state["use_html"]:
            try:
                return await message.answer(text, parse_mode=ParseMode.HTML, **kwargs)
            except TelegramBadRequest:
                state["use_html"] = False
        return await message.answer(sanitize_telegram_plain_text(text), **kwargs)

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

    async def _apply_text(
        capped: str,
        *,
        force: bool = False,
        reply_markup: "InlineKeyboardMarkup | None" = None,
    ) -> None:
        if not capped and reply_markup is None:
            return
        if not capped:
            capped = "…"
        now = time.monotonic()
        if state["sent_msg"] is None:
            state["sent_msg"] = await _send(capped, reply_markup=reply_markup)
            state["last_edit_mono"] = now
            state["last_text"] = capped
            return
        if capped == state["last_text"] and reply_markup is None and not force:
            return
        elapsed = now - state["last_edit_mono"]
        if not force and elapsed < interval and reply_markup is None:
            return
        if capped != state["last_text"] or force:
            if await _edit(capped):
                state["last_text"] = capped
                state["last_edit_mono"] = now
        if reply_markup is not None:
            await _apply_reply_markup(reply_markup)

    async def _apply_reply_markup(reply_markup: "InlineKeyboardMarkup") -> None:
        if state["sent_msg"] is None:
            return
        try:
            await bot.edit_message_reply_markup(
                chat_id=state["sent_msg"].chat.id,
                message_id=state["sent_msg"].message_id,
                reply_markup=reply_markup,
            )
            return
        except TelegramBadRequest:
            logger.warning(
                "telegram_streaming edit_reply_markup failed — fallback answer",
                exc_info=True,
            )
        try:
            text = state["last_text"] or "…"
            state["sent_msg"] = await _send(text, reply_markup=reply_markup)
        except Exception:
            logger.exception("telegram_streaming markup fallback answer failed")

    return TelegramStreamingHandle(
        _apply_text=_apply_text,
        _sent_msg_ref=lambda: state["sent_msg"],
    )


async def send_telegram_streaming_text(
    *,
    bot: "Bot",
    message: "Message",
    settings: Settings,
    openrouter_messages: list[dict[str, Any]],
    models: list[str] | None = None,
    timeout: float | None = None,
    max_tokens: int | None = None,
    text_role: str | None = None,
    temperature: float | None = None,
    http_client: object | None = None,
) -> tuple[ChatCompletionResult, TelegramStreamingHandle]:
    """
    OpenRouter ``stream=True`` + typing indicator + live ``edit_message_text`` (1.8 с).

    Возвращает результат completion и handle (для ``finalize`` с inline-кнопками).
    """
    handle = create_telegram_streaming_handle(message, bot)
    async with bot_typing_indicator(bot, message.chat.id, platform="telegram"):
        result = await ask_ai_messages(
            settings,
            openrouter_messages,
            timeout=timeout,
            max_context_tokens=settings.chat_max_context_tokens_est,
            char_per_token=settings.chat_char_per_token_est,
            http_client=http_client,
            stream_callback=handle.on_stream,
            models=models,
            max_tokens=max_tokens,
            text_role=text_role,
            temperature=temperature,
        )
    return result, handle


__all__ = [
    "TELEGRAM_STREAM_EDIT_INTERVAL_SEC",
    "TelegramStreamingHandle",
    "create_telegram_streaming_handle",
    "send_telegram_streaming_text",
]
