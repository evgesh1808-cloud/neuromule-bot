"""Telegram Share URL для вирусной кнопки под результатом генерации фото (только FREE)."""

from __future__ import annotations

import urllib.parse

from config import settings
from services.tariffs import TariffName, normalize_tariff


def get_photo_share_url(prompt: str, user_id: int) -> str:
    """
    Собирает нативный Telegram Share URL (без скачивания файла на устройство).

    ``url`` — реферальный deep-link бота; ``text`` — подпись для предпросмотра.
    """
    snippet = (prompt or "").strip()[:180]
    share_text = f"🎨 Оцените шедевр, который я сгенерировал по промпту:\n«{snippet}»"
    encoded_text = urllib.parse.quote(share_text)

    username = (settings.telegram_bot_username or "NeuroMule_bot").lstrip("@")
    ref_link = f"https://t.me/{username}?start=ref{int(user_id)}"
    encoded_url = urllib.parse.quote(ref_link, safe="")

    return f"https://t.me/share/url?url={encoded_url}&text={encoded_text}"


def resolve_photo_share_url(
    tariff: TariffName | str | None,
    prompt: str,
    user_id: int,
) -> str | None:
    """Возвращает Share URL только для тарифа FREE; для MINI/SMART/ULTRA — ``None``."""
    tier = tariff if isinstance(tariff, TariffName) else normalize_tariff(tariff)
    if tier is not TariffName.FREE:
        return None
    return get_photo_share_url(prompt, user_id)
