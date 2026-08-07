"""Inline-клавиатура VK «🎨 Открыть Студию» (open_app / open_link fallback)."""

from __future__ import annotations

import json

from config import settings
from content import messages as msg
from platforms.webapp_urls import resolve_image_studio_webapp_url


def vk_image_studio_keyboard_json() -> str | None:
    """
    JSON для ``messages.send(keyboard=...)``.

    * ``VK_MINI_APP_ID`` задан → ``open_app`` (нативный VK Mini App).
    * Иначе при наличии ``WEBAPP_STUDIO_URL`` → ``open_link`` на веб-студию.
    """
    app_id = int(settings.vk_mini_app_id or 0)
    if app_id > 0:
        action: dict[str, object] = {
            "type": "open_app",
            "app_id": app_id,
            "label": msg.BTN_OPEN_STUDIO,
        }
        group_id = int(settings.vk_group_id or 0)
        if group_id > 0:
            action["owner_id"] = -abs(group_id)
        keyboard = {"inline": True, "buttons": [[{"action": action}]]}
        return json.dumps(keyboard, ensure_ascii=False)

    url = resolve_image_studio_webapp_url()
    if not url:
        return None

    keyboard = {
        "inline": True,
        "buttons": [
            [
                {
                    "action": {
                        "type": "open_link",
                        "link": url,
                        "label": msg.BTN_OPEN_STUDIO,
                    }
                }
            ]
        ],
    }
    return json.dumps(keyboard, ensure_ascii=False)
