"""Unit-тесты fal_image_pipeline и new_result_keyboard."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from content import messages as msg
from content.keyboards import (
    new_result_keyboard,
    result_upscale_submenu_keyboard,
)
from content.inline_keyboards import result_format_submenu_keyboard
from services.fal_image_pipeline import (
    _extract_image_url,
    fal_configured,
    generate_fal_i2i_reference,
)


def test_extract_image_url_from_images_list() -> None:
    url = _extract_image_url({"images": [{"url": "https://fal.media/out.png"}]})
    assert url == "https://fal.media/out.png"


def test_extract_image_url_top_level() -> None:
    url = _extract_image_url({"url": "https://fal.media/x.jpg"})
    assert url == "https://fal.media/x.jpg"


@pytest.mark.asyncio
async def test_generate_fal_i2i_reference_two_step() -> None:
    calls: list[tuple[str, dict]] = []

    async def _fake_subscribe(model: str, arguments: dict) -> dict:
        calls.append((model, arguments))
        if model.endswith("flux/schnell"):
            return {"images": [{"url": "https://fal.media/base.png"}]}
        return {"images": [{"url": "https://fal.media/final.png"}]}

    with patch("services.fal_image_pipeline.fal_subscribe", side_effect=_fake_subscribe):
        url = await generate_fal_i2i_reference(
            "portrait in studio",
            "https://user.example/face.jpg",
            seed=42,
        )

    assert url == "https://fal.media/final.png"
    assert len(calls) == 2
    assert calls[0][0] == "fal-ai/flux/schnell"
    assert calls[0][1]["prompt"] == "portrait in studio"
    assert calls[0][1]["image_size"] == "square_hd"
    assert calls[0][1]["sync_mode"] is True
    assert calls[0][1]["seed"] == 42
    assert calls[1][0] == "fal-ai/fash-cron/face-swap"
    assert calls[1][1]["base_image_url"] == "https://fal.media/base.png"
    assert calls[1][1]["swap_image_url"] == "https://user.example/face.jpg"


def test_new_result_keyboard_layout() -> None:
    kb = new_result_keyboard(task_id="t1")
    rows = kb.inline_keyboard
    assert len(rows) == 3
    assert rows[0][0].text == "🔍 Улучшить"
    assert rows[0][0].callback_data == msg.CB_RESULT_UPSCALE
    assert rows[0][1].callback_data == msg.CB_RESULT_REPEAT_PHOTO
    assert rows[1][0].callback_data == msg.CB_RESULT_ANIMATE
    assert rows[1][1].callback_data == msg.CB_RESULT_CHANGE_FORMAT
    assert rows[2][0].callback_data == msg.CB_PHOTO_REFINE


def test_upscale_submenu_has_back() -> None:
    kb = result_upscale_submenu_keyboard()
    assert kb.inline_keyboard[0][0].text == "🔍 Сделать чётче х2 (1 💎)"
    assert kb.inline_keyboard[-1][0].callback_data == msg.CB_RESULT_GRID_BACK


def test_format_submenu_has_aspect_options() -> None:
    kb = result_format_submenu_keyboard()
    assert len(kb.inline_keyboard) == len(msg.IMAGE_ASPECT_OPTIONS) + 1


def test_fal_configured_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.fal_image_pipeline._fal_key", lambda: "")
    assert fal_configured() is False
