"""Regression: Reply-кнопки меню и WebApp URL в create_menu."""

from __future__ import annotations

from config import settings
from content import messages as msg
from platforms.telegram_keyboards import create_menu, image_studio_webapp_button
from platforms.telegram_utils import (
    CreateMenuButtonFilter,
    ReplyButtonFilter,
    normalize_reply_button_text,
)
from platforms.webapp_urls import is_valid_telegram_webapp_url, resolve_image_studio_webapp_url
from unittest.mock import MagicMock

import pytest


def test_is_valid_telegram_webapp_url_https_only() -> None:
    assert is_valid_telegram_webapp_url("https://studio.example/webapp/") is True
    assert is_valid_telegram_webapp_url("http://127.0.0.1:8000/webapp/") is False
    assert is_valid_telegram_webapp_url("") is False


def test_resolve_studio_rejects_http_api_base() -> None:
    object.__setattr__(settings, "webapp_studio_url", None)
    object.__setattr__(settings, "webapp_shop_url", None)
    object.__setattr__(settings, "mini_app_api_base_url", "http://127.0.0.1:8000")
    assert resolve_image_studio_webapp_url() is None
    assert image_studio_webapp_button() is None


def test_create_menu_fallback_when_shop_url_http_only() -> None:
    object.__setattr__(settings, "is_webapp_enabled", True)
    object.__setattr__(settings, "webapp_shop_url", "http://127.0.0.1:8000/shop/")
    object.__setattr__(settings, "webapp_studio_url", None)
    kb = create_menu()
    flat = [b for row in kb.inline_keyboard for b in row]
    assert any(b.callback_data for b in flat)
    assert not any(b.web_app is not None for b in flat)


@pytest.mark.asyncio
async def test_create_menu_button_filter_matches_fe0f_variant() -> None:
    filt = CreateMenuButtonFilter()
    message = MagicMock()
    message.text = "🎨️ Создать"
    assert await filt(message) is True
    assert normalize_reply_button_text(message.text) == normalize_reply_button_text(msg.BTN_CREATE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label",
    [
        msg.BTN_TARIFFS,
        msg.BTN_PROFILE,
        msg.BTN_SUPPORT,
        msg.BTN_DAILY_ADVICE,
        msg.BTN_REPLY_NEUROTEXT,
        msg.BTN_REPLY_VIDEO,
    ],
)
async def test_reply_button_filter_matches_fe0f_variant(label: str) -> None:
    filt = ReplyButtonFilter(label)
    message = MagicMock()
    if label and ord(label[0]) > 0x1F000:
        message.text = label[0] + "\ufe0f" + label[1:]
    else:
        message.text = label
    assert await filt(message) is True
