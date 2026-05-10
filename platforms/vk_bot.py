"""VK-интерфейс (vkbottle). Те же services (БД + OpenRouter), что и у Telegram."""
from __future__ import annotations

import asyncio
import random

from config import settings
from content import messages as msg
from services.ai_text import ask_ai_text
from services.app_logging import setup_logging
from services.repository import ensure_user, init_db, try_consume_energy, update_balance


def run_vk() -> None:
    if not settings.vk_token.strip():
        raise RuntimeError("Задайте VK_TOKEN в .env для запуска VK-бота.")
    if not settings.openrouter_key.strip():
        raise RuntimeError("Задайте OPENROUTER_API_KEY в .env (общий ключ для AI).")

    try:
        from vkbottle.bot import Bot, Message
    except ImportError as exc:
        raise RuntimeError("Установите vkbottle: pip install vkbottle") from exc

    setup_logging(settings)
    asyncio.run(init_db(settings.promo_seeds))
    bot = Bot(token=settings.vk_token)

    @bot.on.message()
    async def handler(message: Message) -> None:
        text = (message.text or "").strip()
        uid = message.from_id

        if text.startswith("/start"):
            await ensure_user(uid)
            await message.answer(msg.TXT_VK_START.format(bot_name=settings.bot_name))
            return

        if not text or text.startswith("/"):
            return

        if text.lower() in msg.EASTER_THANKS_TRIGGERS:
            await message.answer(random.choice(msg.EASTER_THANKS_REPLIES))
            return

        await ensure_user(uid)
        if not await try_consume_energy(uid, settings.cost_text_pro):
            await message.answer(msg.TXT_INSUFFICIENT_BALANCE)
            return

        try:
            answer = await ask_ai_text(settings, text)
        except Exception:
            await update_balance(uid, "energy", settings.cost_text_pro)
            await message.answer(msg.TXT_GEN_JOB_FAILED)
            return
        await message.answer(answer)

    print(f"{settings.bot_name} vk: polling started.")
    bot.run_forever()
