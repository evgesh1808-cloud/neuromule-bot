"""Перехват промпта после меню фото без выбора inline-модели."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from content import messages as msg
from platforms.image_menu_flow import (
    IMAGE_MODEL_MENU_PENDING_KEY,
    FREE_AUTO_IMAGE_INTERCEPT_MIN_LEN,
    can_intercept_text_as_image_prompt,
    is_image_model_menu_pending,
    text_looks_like_image_prompt,
)
from platforms.telegram_states import UserFlow


@pytest.mark.asyncio
async def test_can_intercept_when_menu_pending_and_text_prompt() -> None:
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={IMAGE_MODEL_MENU_PENDING_KEY: True})
    state.get_state = AsyncMock(return_value=UserFlow.waiting_for_text_prompt.state)

    message = MagicMock()
    message.from_user.id = 42
    message.text = "кот на луне в стиле аниме"

    assert await can_intercept_text_as_image_prompt(message, state) is True


@pytest.mark.asyncio
async def test_no_intercept_without_pending_flag() -> None:
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    state.get_state = AsyncMock(return_value=UserFlow.waiting_for_text_prompt.state)

    message = MagicMock()
    message.text = "кот на луне"

    assert await can_intercept_text_as_image_prompt(message, state) is False


@pytest.mark.asyncio
async def test_no_intercept_for_menu_button_text() -> None:
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={IMAGE_MODEL_MENU_PENDING_KEY: True})
    state.get_state = AsyncMock(return_value=UserFlow.waiting_for_text_prompt.state)

    message = MagicMock()
    message.from_user.id = 42
    message.text = msg.BTN_REPLY_NEUROTEXT

    assert await can_intercept_text_as_image_prompt(message, state) is False


@pytest.mark.asyncio
async def test_intercept_in_dedicated_image_model_pick_state() -> None:
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    state.get_state = AsyncMock(return_value=UserFlow.waiting_for_image_model_pick.state)

    message = MagicMock()
    message.from_user.id = 42
    message.text = "длинный промпт для фото " * 50

    assert await can_intercept_text_as_image_prompt(message, state) is True


@pytest.mark.asyncio
async def test_no_intercept_when_already_waiting_for_photo() -> None:
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={IMAGE_MODEL_MENU_PENDING_KEY: True})
    state.get_state = AsyncMock(return_value=UserFlow.waiting_for_photo.state)

    message = MagicMock()
    message.text = "кот на луне"

    assert await can_intercept_text_as_image_prompt(message, state) is False


def test_is_image_model_menu_pending() -> None:
    assert is_image_model_menu_pending({IMAGE_MODEL_MENU_PENDING_KEY: True}) is True
    assert is_image_model_menu_pending({}) is False


def test_text_looks_like_image_prompt_markers() -> None:
    assert text_looks_like_image_prompt(
        "portrait photo, soft light, 9:16, realistic face reference"
    )
    assert not text_looks_like_image_prompt("Привет, как дела?")


@pytest.mark.asyncio
async def test_free_long_text_intercept_without_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _free(_uid: int) -> bool:
        return True

    monkeypatch.setattr(
        "services.billing.free_tier_gates.is_free_user",
        _free,
    )

    state = AsyncMock()
    state.get_data = AsyncMock(return_value={"text_role": "standard"})
    state.get_state = AsyncMock(return_value=None)

    message = MagicMock()
    message.from_user.id = 42
    message.text = "x" * (FREE_AUTO_IMAGE_INTERCEPT_MIN_LEN + 10)

    assert await can_intercept_text_as_image_prompt(message, state) is True


@pytest.mark.asyncio
async def test_paid_user_no_auto_intercept_without_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _paid(_uid: int) -> bool:
        return False

    monkeypatch.setattr(
        "services.billing.free_tier_gates.is_free_user",
        _paid,
    )

    state = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    state.get_state = AsyncMock(return_value=None)

    message = MagicMock()
    message.from_user.id = 42
    message.text = "x" * 500

    assert await can_intercept_text_as_image_prompt(message, state) is False
