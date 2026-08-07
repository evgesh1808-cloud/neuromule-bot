"""Валидация подписи Mini App (Telegram initData / VK launch params)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from api.auth import (
    TelegramInitDataError,
    TelegramWebAppUser,
    extract_init_data_from_headers,
    validate_telegram_init_data,
)
from api.vk_auth import (
    VkLaunchParamsError,
    VkWebAppUser,
    extract_vk_launch_params_from_headers,
    validate_vk_launch_params,
)


@dataclass(frozen=True, slots=True)
class WebAppAuthContext:
    user_id: int
    platform: Literal["telegram", "vk"]
    chat_id: int
    first_name: str | None = None


def validate_tg_init_data(init_data: str) -> TelegramWebAppUser:
    """Проверка HMAC Telegram WebApp ``initData``."""
    return validate_telegram_init_data(init_data)


def validate_vk_sign(query: str) -> VkWebAppUser:
    """Проверка MD5-подписи VK launch params."""
    return validate_vk_launch_params(query)


def resolve_webapp_auth_from_headers(
    *,
    authorization: str | None,
    x_telegram_init_data: str | None = None,
    x_vk_launch_params: str | None = None,
) -> WebAppAuthContext:
    auth = (authorization or "").strip()
    scheme = auth.split(" ", 1)[0].lower() if auth else ""

    if scheme == "tma" or x_telegram_init_data:
        init_data = extract_init_data_from_headers(
            authorization=authorization,
            x_telegram_init_data=x_telegram_init_data,
        )
        user = validate_tg_init_data(init_data)
        raw = user.raw_user.get("first_name")
        first_name = str(raw).strip() if isinstance(raw, str) and raw.strip() else None
        return WebAppAuthContext(
            user_id=user.telegram_id,
            platform="telegram",
            chat_id=user.telegram_id,
            first_name=first_name,
        )

    if scheme == "vk" or x_vk_launch_params:
        query = extract_vk_launch_params_from_headers(
            authorization=authorization,
            x_vk_launch_params=x_vk_launch_params,
        )
        vk_user = validate_vk_sign(query)
        return WebAppAuthContext(
            user_id=vk_user.vk_user_id,
            platform="vk",
            chat_id=vk_user.vk_user_id,
            first_name=None,
        )

    raise TelegramInitDataError("Missing auth headers")


__all__ = [
    "TelegramInitDataError",
    "VkLaunchParamsError",
    "WebAppAuthContext",
    "resolve_webapp_auth_from_headers",
    "validate_tg_init_data",
    "validate_vk_sign",
]
