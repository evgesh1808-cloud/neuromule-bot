"""Короткие коды платформы для таблицы ``dialog_messages``."""

from __future__ import annotations

DIALOG_PLATFORM_TELEGRAM = "tg"
DIALOG_PLATFORM_VK = "vk"
DIALOG_PLATFORM_MAX = "max"

DIALOG_PLATFORMS: frozenset[str] = frozenset(
    {
        DIALOG_PLATFORM_TELEGRAM,
        DIALOG_PLATFORM_VK,
        DIALOG_PLATFORM_MAX,
    }
)

DEFAULT_DIALOG_PLATFORM = DIALOG_PLATFORM_TELEGRAM

_IDENTITY_TO_DIALOG: dict[str, str] = {
    "telegram": DIALOG_PLATFORM_TELEGRAM,
    "tg": DIALOG_PLATFORM_TELEGRAM,
    "vk": DIALOG_PLATFORM_VK,
    "max": DIALOG_PLATFORM_MAX,
}


def normalize_dialog_platform(platform: str | None) -> str:
    """Нормализует код платформы для ``dialog_messages`` (``tg`` / ``vk`` / ``max``)."""
    key = (platform or DEFAULT_DIALOG_PLATFORM).strip().lower()
    resolved = _IDENTITY_TO_DIALOG.get(key, key)
    if resolved not in DIALOG_PLATFORMS:
        raise ValueError(f"unsupported dialog platform: {platform!r}")
    return resolved
