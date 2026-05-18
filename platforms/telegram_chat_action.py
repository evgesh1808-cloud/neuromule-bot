"""
Цикл «chat action» (typing / upload_photo / …) на время долгой генерации.

Telegram гасит chat action примерно через 5 секунд, поэтому цикл повторно шлёт
тот же статус каждые ``interval`` секунд (по умолчанию 4.5), параллельно основной
задаче. Цикл стартует синхронно с пользовательским текстом «Мул пошёл в облака…»
и завершается сразу после возврата из ``async with``.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Literal

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

ChatActionName = Literal[
    "typing",
    "upload_photo",
    "upload_audio",
    "upload_document",
    "upload_video",
]


@asynccontextmanager
async def chat_action_loop(
    bot: "Bot",
    chat_id: int,
    action: ChatActionName,
    interval: float = 4.5,
) -> AsyncIterator[None]:
    """
    Запускает фоновый ``asyncio.create_task`` с периодической отправкой ``action``.

    Вход:
        bot — aiogram Bot, через который шлём ``send_chat_action``.
        chat_id — целевой чат.
        action — статус активности (typing / upload_photo / upload_audio / upload_document).
        interval — период повторной отправки (4–5 секунд, чтобы Telegram не гасил статус).

    Внутри ``async with`` основная корутина продолжает работу (запрос к API, рендер PDF и т.п.);
    цикл идёт параллельно. По выходу из контекста цикл немедленно останавливается, его задача
    дожидается и любые сетевые ошибки в нём подавляются (диагностика — в DEBUG-лог).
    """
    stop_event = asyncio.Event()

    async def _runner() -> None:
        try:
            await bot.send_chat_action(chat_id, action)
        except Exception:
            logger.debug("chat_action initial send failed chat_id=%s action=%s", chat_id, action, exc_info=True)
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await bot.send_chat_action(chat_id, action)
            except Exception:
                logger.debug("chat_action loop send failed chat_id=%s action=%s", chat_id, action, exc_info=True)

    task = asyncio.create_task(_runner())
    try:
        yield
    finally:
        stop_event.set()
        try:
            await task
        except Exception:
            logger.debug("chat_action task await failed chat_id=%s", chat_id, exc_info=True)
