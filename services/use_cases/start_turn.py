"""
Use-case: /start — регистрация пользователя, проверка подписки на канал (через колбэк), реферал из deep-link.

Платформенный слой передаёт ``is_subscribed`` (например, ``get_chat_member`` в Telegram).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from config import Settings
from services.repository import ensure_user, try_set_referrer


class StartFlowOutcome(str, Enum):
    """Какой сценарий показать после ``/start``."""

    NEED_CHANNEL = "need_channel"
    WELCOME_MAIN_MENU = "welcome_main_menu"


@dataclass(frozen=True)
class StartTurnResult:
    outcome: StartFlowOutcome
    template_kwargs: dict[str, object]


def parse_telegram_start_ref(start_command_text: str | None) -> int | None:
    """
    Извлекает inviter id из ``/start ref_<id>`` (Telegram deep-link).

    Возвращает ``None``, если аргумента нет или формат не ``ref_<int>``.
    """
    if not start_command_text:
        return None
    parts = start_command_text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    arg = parts[1].strip()
    if not arg.startswith("ref_"):
        return None
    try:
        return int(arg[4:])
    except ValueError:
        return None


async def run_start_turn(
    settings: Settings,
    user_id: int,
    username: str | None,
    start_command_text: str | None,
    *,
    is_subscribed: Callable[[int], Awaitable[bool]],
) -> StartTurnResult:
    """
    Обеспечивает строку в ``users``, при подписке — учёт реферера из ``/start``.

    Вход:
        settings — конфиг.
        user_id — Telegram id.
        username — ``from_user.username`` или ``None``.
        start_command_text — полный текст сообщения ``/start ...``.
        is_subscribed — асинхронная проверка подписки на канал: ``async (uid) -> bool`` (например ``get_chat_member``).

    Возвращает:
        ``StartTurnResult`` с исходом и ``template_kwargs`` для ``.format`` текстов приветствия.
    """
    await ensure_user(user_id, username)
    template_kwargs: dict[str, object] = dict(
        channel_url=settings.channel_url,
        text_daily_limit=settings.free_daily_chat_limit,
        photo_daily_limit=settings.free_daily_photo_limit,
    )
    if not await is_subscribed(user_id):
        return StartTurnResult(StartFlowOutcome.NEED_CHANNEL, template_kwargs)

    inviter_id = parse_telegram_start_ref(start_command_text)
    if inviter_id is not None:
        await try_set_referrer(user_id, inviter_id)

    return StartTurnResult(StartFlowOutcome.WELCOME_MAIN_MENU, template_kwargs)
