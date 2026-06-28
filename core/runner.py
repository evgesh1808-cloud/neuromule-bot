"""Одновременный запуск API + VK + Discord (без Telegram — см. NEUROMULE_PLATFORM=telegram)."""
from __future__ import annotations

import asyncio
import logging

from config import settings

logger = logging.getLogger(__name__)


async def _run_summarizer_api() -> None:
    import uvicorn

    from core.api import app

    config = uvicorn.Config(
        app,
        host=settings.summarizer_api_host,
        port=settings.summarizer_api_port,
        log_level="info",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    logger.info("Summarizer API: http://%s:%s", settings.summarizer_api_host, settings.summarizer_api_port)
    await server.serve()


async def _run_vk_blocking() -> None:
    from platforms.summarizer_vk import run_vk_summarizer_blocking

    await asyncio.to_thread(run_vk_summarizer_blocking)


async def run_summarizer_platforms() -> None:
    """
    Режим ``NEUROMULE_PLATFORM=summarizer``.

    Telegram намеренно НЕ стартует (один TG_TOKEN → иначе Conflict с основным ботом).
    Telegram-саммари: ``summarizer_router`` в ``platforms/telegram_bot.py``.
    """
    if not summarizer_llm_configured():
        raise RuntimeError("Задайте OPENAI_API_KEY или OPENROUTER_API_KEY в .env")

    async with asyncio.TaskGroup() as group:
        group.create_task(_run_summarizer_api())
        logger.info("Summarizer: API enabled; Telegram disabled (use NEUROMULE_PLATFORM=telegram)")

        if settings.vk_token.strip():
            group.create_task(_run_vk_blocking())
            logger.info("Summarizer VK: enabled")
        else:
            logger.warning("Summarizer VK: пропуск (нет VK_TOKEN)")

        if settings.discord_token.strip():
            from platforms.summarizer_discord import run_discord_summarizer

            group.create_task(run_discord_summarizer())
            logger.info("Summarizer Discord: enabled")
        else:
            logger.warning("Summarizer Discord: пропуск (нет DISCORD_TOKEN)")
