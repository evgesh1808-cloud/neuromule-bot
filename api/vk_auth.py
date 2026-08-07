"""Валидация launch params VK Mini App (MD5 sign)."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from urllib.parse import parse_qsl

from config import settings

logger = logging.getLogger(__name__)

_VK_AUTH_SCHEME = "vk"


class VkLaunchParamsError(ValueError):
    """Невалидная подпись или отсутствующие поля VK launch params."""


@dataclass(frozen=True, slots=True)
class VkWebAppUser:
    vk_user_id: int
    raw_params: dict[str, str]


def _vk_secret() -> str:
    secret = (settings.vk_mini_app_secret or "").strip()
    if not secret:
        raise VkLaunchParamsError("VK_MINI_APP_SECRET is not configured")
    return secret


def _parse_launch_query(query: str) -> dict[str, str]:
    cleaned = (query or "").strip().lstrip("?")
    if not cleaned:
        raise VkLaunchParamsError("VK launch params are empty")
    pairs = dict(parse_qsl(cleaned, keep_blank_values=True, strict_parsing=True))
    if not pairs:
        raise VkLaunchParamsError("VK launch params have no fields")
    return pairs


def validate_vk_launch_params(query: str) -> VkWebAppUser:
    """
    Проверяет ``sign`` для VK Mini App.

    Алгоритм: https://dev.vk.com/mini-apps/development/launch-params-sign
    """
    params = _parse_launch_query(query)
    received_sign = (params.pop("sign", "") or "").strip().lower()
    if not received_sign:
        raise VkLaunchParamsError("VK launch params missing sign")

    base = "&".join(f"{key}={params[key]}" for key in sorted(params))
    expected = hashlib.md5((base + _vk_secret()).encode("utf-8")).hexdigest().lower()
    if expected != received_sign:
        raise VkLaunchParamsError("VK launch params sign mismatch")

    user_raw = (params.get("vk_user_id") or "").strip()
    try:
        vk_user_id = int(user_raw)
    except ValueError as exc:
        raise VkLaunchParamsError("vk_user_id invalid") from exc
    if vk_user_id <= 0:
        raise VkLaunchParamsError("vk_user_id invalid")

    return VkWebAppUser(vk_user_id=vk_user_id, raw_params=params)


def extract_vk_launch_params_from_headers(
    *,
    authorization: str | None,
    x_vk_launch_params: str | None,
) -> str:
    if x_vk_launch_params and x_vk_launch_params.strip():
        return x_vk_launch_params.strip()
    if authorization:
        scheme, _, remainder = authorization.partition(" ")
        if scheme.lower() == _VK_AUTH_SCHEME and remainder.strip():
            return remainder.strip()
    raise VkLaunchParamsError("Missing VK launch params header")
