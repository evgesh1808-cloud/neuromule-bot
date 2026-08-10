"""Unit-тесты fal_image_pipeline и new_result_keyboard."""

from __future__ import annotations

import pytest

from content import messages as msg
from content.inline_keyboards import (
    new_result_keyboard,
    result_format_submenu_keyboard,
    result_upscale_submenu_keyboard,
)
from services.fal_image_pipeline import _extract_image_url, fal_configured


def test_extract_image_url_from_images_list() -> None:
    url = _extract_image_url({"images": [{"url": "https://fal.media/out.png"}]})
    assert url == "https://fal.media/out.png"


def test_extract_image_url_top_level() -> None:
    url = _extract_image_url({"url": "https://fal.media/x.jpg"})
    assert url == "https://fal.media/x.jpg"


def test_new_result_keyboard_layout() -> None:
    kb = new_result_keyboard(task_id="t1")
    rows = kb.inline_keyboard
    assert len(rows) == 3
    assert rows[0][0].callback_data == msg.CB_RESULT_UPSCALE
    assert rows[0][1].callback_data == msg.CB_RESULT_REPEAT_PHOTO
    assert rows[1][0].callback_data == msg.CB_RESULT_ANIMATE
    assert rows[1][1].callback_data == msg.CB_RESULT_CHANGE_FORMAT
    assert rows[2][0].callback_data == msg.CB_PHOTO_REFINE


def test_upscale_submenu_has_back() -> None:
    kb = result_upscale_submenu_keyboard()
    assert kb.inline_keyboard[-1][0].callback_data == msg.CB_RESULT_GRID_BACK


def test_format_submenu_has_aspect_options() -> None:
    kb = result_format_submenu_keyboard()
    assert len(kb.inline_keyboard) == len(msg.IMAGE_ASPECT_OPTIONS) + 1


def test_fal_configured_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.fal_image_pipeline._fal_key", lambda: "")
    assert fal_configured() is False
