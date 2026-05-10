"""
Use-case: экран «Личный кабинет» — текст со статистикой и реферальной ссылкой.
"""

from __future__ import annotations

from dataclasses import dataclass

from config import Settings
from content import messages as msg
from services.repository import get_user_row, referrals_count


@dataclass(frozen=True)
class CabinetView:
    """Текст сообщения для ответа пользователю."""

    text: str


async def build_cabinet_view(settings: Settings, user_id: int) -> CabinetView:
    """
    Загружает пользователя, число приглашённых, собирает текст по шаблону.

    Вход:
        settings — конфиг (username бота для deep-link).
        user_id — Telegram user id.

    Возвращает:
        ``CabinetView`` с готовым ``text``.
    """
    row = await get_user_row(user_id)
    invites = await referrals_count(user_id)
    ref_link = f"https://t.me/{settings.telegram_bot_username.lstrip('@')}?start=ref_{user_id}"
    text = msg.TXT_CABINET_TEMPLATE.format(
        user_id=user_id,
        energy=row.energy,
        tariff=row.tariff,
        invites=invites,
        ref_link=ref_link,
    )
    return CabinetView(text=text)
