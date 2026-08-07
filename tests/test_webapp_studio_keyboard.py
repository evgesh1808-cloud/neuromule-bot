"""Кнопка «🎨 Открыть Студию» в меню и VK-клавиатуре."""

from __future__ import annotations

import json

from config import settings
from content import messages as msg
from platforms.telegram_keyboards import create_menu, image_studio_webapp_button
from platforms.vk_studio_keyboard import vk_image_studio_keyboard_json


def test_image_studio_webapp_button_none_without_url() -> None:
    object.__setattr__(settings, "webapp_studio_url", None)
    object.__setattr__(settings, "webapp_shop_url", None)
    object.__setattr__(settings, "mini_app_api_base_url", "")
    assert image_studio_webapp_button() is None


def test_create_menu_includes_studio_when_url_set() -> None:
    object.__setattr__(settings, "is_webapp_enabled", False)
    object.__setattr__(settings, "webapp_studio_url", "https://studio.example/webapp/")
    kb = create_menu()
    first = kb.inline_keyboard[0][0]
    assert first.text == msg.BTN_OPEN_STUDIO
    assert first.web_app is not None
    assert first.web_app.url.startswith("https://studio.example/")


def test_vk_studio_keyboard_open_app() -> None:
    object.__setattr__(settings, "vk_mini_app_id", 7654321)
    object.__setattr__(settings, "vk_group_id", 12345)
    raw = vk_image_studio_keyboard_json()
    assert raw is not None
    data = json.loads(raw)
    action = data["buttons"][0][0]["action"]
    assert action["type"] == "open_app"
    assert action["app_id"] == 7654321
    assert action["owner_id"] == -12345
