"""Runtime-хранилище vkbottle Bot для фоновых воркеров генерации."""

from __future__ import annotations

from typing import Any

from platforms.vk_messages import vk_send_message

_vk_bot: Any | None = None


def set_vk_bot(bot: Any) -> None:
    global _vk_bot
    _vk_bot = bot


def get_vk_bot() -> Any | None:
    return _vk_bot


async def notify_vk_user(peer_id: int, text: str) -> None:
    bot = get_vk_bot()
    if bot is None:
        return
    try:
        await vk_send_message(bot, peer_id, text)
    except Exception:
        import logging

        logging.getLogger(__name__).debug(
            "notify_vk_user failed peer_id=%s",
            peer_id,
            exc_info=True,
        )
