"""Нормализация Reply-кнопки «Изображение» (FE0F / VS16)."""

from __future__ import annotations

from content import messages as msg
from platforms.telegram_utils import (
    is_image_reply_button_text,
    is_reply_nav_button_text,
    normalize_reply_button_text,
)


def test_normalize_strips_fe0f() -> None:
    assert normalize_reply_button_text("🖼️ Изображение") == normalize_reply_button_text(
        "🖼 Изображение"
    )


def test_image_button_matches_both_emoji_forms() -> None:
    assert is_image_reply_button_text(msg.BTN_REPLY_IMAGE)
    assert is_image_reply_button_text("🖼️ Изображение")
    assert not is_image_reply_button_text("🎨 Создать")


def test_create_menu_grid_uses_canonical_image_label() -> None:
    labels = [label for label, _cb in msg.CREATE_MENU_GRID]
    assert msg.BTN_REPLY_IMAGE in labels
    assert is_reply_nav_button_text("🖼️ Изображение")
