"""VK-интерфейс (vkbottle). Те же services (БД + OpenRouter), что и у Telegram."""
from __future__ import annotations

import asyncio
import logging
import random

from config import settings
from content import messages as msg
from platforms.vk_messages import vk_answer
from services.ai_text import ask_ai_text
from services.app_logging import setup_logging
from services.repository import ensure_user, init_db, try_consume_energy, update_balance

logger = logging.getLogger(__name__)


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

    from platforms.vk_photo_flow import (
        clear_vk_image_mode,
        enter_vk_image_mode,
        handle_vk_photo_message,
        handle_vk_photo_refine_event,
    )
    from platforms.vk_photo_keyboard import parse_vk_refine_payload
    from platforms.vk_runtime import set_vk_bot

    set_vk_bot(bot)

    try:
        from vkbottle import GroupEventType, GroupTypes
    except ImportError:
        GroupEventType = None  # type: ignore[misc, assignment]
        GroupTypes = None  # type: ignore[misc, assignment]

    if GroupEventType is not None and GroupTypes is not None:

        @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=GroupTypes.MessageEvent)
        async def vk_message_event(event: GroupTypes.MessageEvent) -> None:
            payload = getattr(event.object, "payload", None)
            if not parse_vk_refine_payload(payload):
                return
            peer_id = int(getattr(event.object, "peer_id", 0) or 0)
            user_id = int(getattr(event.object, "user_id", 0) or 0)
            if peer_id <= 0 or user_id <= 0:
                return
            try:
                await bot.api.messages.send_message_event_answer(
                    event_id=event.object.event_id,
                    user_id=user_id,
                    peer_id=peer_id,
                )
            except Exception:
                logger.exception("vk message_event answer failed peer_id=%s", peer_id)
            await handle_vk_photo_refine_event(peer_id=peer_id, user_id=user_id, bot=bot)

    @bot.on.message()
    async def handler(message: Message) -> None:
        text = (message.text or "").strip()
        uid = message.from_id
        peer_id = message.peer_id

        if text.startswith("/start"):
            await ensure_user(uid)
            clear_vk_image_mode(peer_id)
            from platforms.vk_studio_keyboard import vk_image_studio_keyboard_json

            studio_kb = vk_image_studio_keyboard_json()
            start_text = msg.TXT_VK_START.format(bot_name=settings.bot_name)
            if studio_kb:
                await bot.api.messages.send(
                    peer_id=peer_id,
                    message=start_text,
                    random_id=random.randint(1, 2_000_000_000),
                    keyboard=studio_kb,
                )
            else:
                await vk_answer(message, start_text)
            return

        if text.lower() in {"/image", "изображение"}:
            await ensure_user(uid)
            enter_vk_image_mode(peer_id)
            await vk_answer(message, msg.TXT_CREATE_IMAGE_AFTER_MODEL)
            return

        if await handle_vk_photo_message(message):
            return

        if not text or text.startswith("/"):
            return

        if text.lower() in msg.EASTER_THANKS_TRIGGERS:
            await message.answer(random.choice(msg.EASTER_THANKS_REPLIES))
            return

        from services.agent_intent_dispatch import try_agent_image_intent_vk

        if await try_agent_image_intent_vk(message):
            return

        await ensure_user(uid)
        if not await try_consume_energy(uid, settings.cost_text_pro):
            await vk_answer(message, msg.TXT_INSUFFICIENT_BALANCE)
            return

        try:
            answer = await ask_ai_text(settings, text)
        except Exception:
            await update_balance(uid, "energy", settings.cost_text_pro)
            await vk_answer(message, msg.TXT_GEN_JOB_FAILED)
            return
        await vk_answer(message, answer)

    print(f"{settings.bot_name} vk: polling started.")
    bot.run_forever()
