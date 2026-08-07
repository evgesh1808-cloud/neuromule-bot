"""Inline-клавиатура под сгенерированным фото VK (callback «✏️ Доработать»)."""

from __future__ import annotations

import json

from content import messages as msg

VK_REFINE_PAYLOAD_KEY = "cmd"


def vk_photo_refine_keyboard_json() -> str:
    """JSON для ``messages.send(keyboard=...)`` — inline callback без reply_to."""
    payload = json.dumps({VK_REFINE_PAYLOAD_KEY: msg.CB_PHOTO_REFINE}, ensure_ascii=False)
    keyboard = {
        "inline": True,
        "buttons": [
            [
                {
                    "action": {
                        "type": "callback",
                        "label": msg.BTN_PHOTO_REFINE,
                        "payload": payload,
                    }
                }
            ]
        ],
    }
    return json.dumps(keyboard, ensure_ascii=False)


def parse_vk_refine_payload(raw: object) -> bool:
    """True если payload — нажатие «✏️ Доработать»."""
    if raw is None:
        return False
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return False
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text == msg.CB_PHOTO_REFINE
        if not isinstance(data, dict):
            return False
        return data.get(VK_REFINE_PAYLOAD_KEY) == msg.CB_PHOTO_REFINE
    if isinstance(raw, dict):
        return raw.get(VK_REFINE_PAYLOAD_KEY) == msg.CB_PHOTO_REFINE
    return False
