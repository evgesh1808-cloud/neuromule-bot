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
    looks_like_studio_image_request,
    message_looks_like_photo_prompt,
    normalize_image_prompt_text,
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


@pytest.mark.asyncio
async def test_intercept_when_model_selected_but_state_lost() -> None:
    state = AsyncMock()
    state.get_data = AsyncMock(
        return_value={
            "image_model_id": "free_photo",
            "image_model_label": "Flux FREE",
        }
    )
    state.get_state = AsyncMock(return_value=None)

    message = MagicMock()
    message.from_user.id = 42
    message.text = "кот на луне в стиле аниме"

    assert await can_intercept_text_as_image_prompt(message, state) is True


@pytest.mark.asyncio
async def test_studio_logo_prompt_is_intercepted() -> None:
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={"text_role": "standard"})
    state.get_state = AsyncMock(return_value=UserFlow.waiting_for_text_prompt.state)

    prompt = (
        'Генерация логотипа студии (Формат 1:1):A powerful esports gaming logo for "MULENDEEVA GAMES" '
        "studio. 1:1 aspect, cyberpunk, masterpiece, 8k resolution"
    )
    message = MagicMock()
    message.from_user.id = 42
    message.text = prompt

    assert await can_intercept_text_as_image_prompt(message, state) is True


def test_normalize_studio_logo_prompt_prefix() -> None:
    raw = "Генерация логотипа студии (Формат 1:1):A powerful esports logo"
    assert normalize_image_prompt_text(raw) == "A powerful esports logo"
    assert looks_like_studio_image_request(raw) is True


def test_message_looks_like_photo_prompt_studio() -> None:
    raw = "Генерация логотипа студии (Формат 1:1):logo prompt"
    assert message_looks_like_photo_prompt(raw) is True


def test_is_image_model_menu_pending() -> None:
    assert is_image_model_menu_pending({IMAGE_MODEL_MENU_PENDING_KEY: True}) is True
    assert is_image_model_menu_pending({}) is False


def test_text_looks_like_image_prompt_markers() -> None:
    assert text_looks_like_image_prompt(
        "portrait photo, soft light, 9:16, realistic face reference"
    )
    assert not text_looks_like_image_prompt("Привет, как дела?")


@pytest.mark.asyncio
async def test_free_long_text_not_intercepted_without_menu(monkeypatch: pytest.MonkeyPatch) -> None:
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

    assert await can_intercept_text_as_image_prompt(message, state) is False


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


@pytest.mark.asyncio
async def test_present_image_menu_blocks_free_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platforms.image_menu_flow import present_image_model_menu

    monkeypatch.setattr(
        "services.billing.free_tier_gates.is_free_user",
        AsyncMock(return_value=True),
    )
    blocked = AsyncMock()
    monkeypatch.setattr(
        "platforms.telegram_utils.send_free_create_blocked",
        blocked,
    )

    message = MagicMock()
    message.answer = AsyncMock()
    state = AsyncMock()

    await present_image_model_menu(message, state, user_id=42)

    blocked.assert_awaited_once_with(message)
    message.answer.assert_not_awaited()
    state.set_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_present_image_menu_sent_even_if_fsm_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platforms.image_menu_flow import present_image_model_menu

    class _Row:
        tariff = "smart"

    monkeypatch.setattr(
        "services.billing.free_tier_gates.is_free_user",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "platforms.image_menu_flow.get_user_row",
        AsyncMock(return_value=_Row()),
    )
    monkeypatch.setattr(
        "services.billing.daily_quotas.get_free_photo_snapshot",
        AsyncMock(return_value=MagicMock(used=0, day="2026-08-04")),
    )
    monkeypatch.setattr(
        "services.billing.daily_quotas.quota_day",
        lambda: "2026-08-04",
    )

    message = MagicMock()
    message.answer = AsyncMock()
    state = AsyncMock()
    state.set_state = AsyncMock(side_effect=RuntimeError("redis down"))
    state.update_data = AsyncMock()

    await present_image_model_menu(message, state, user_id=999_001)

    assert message.answer.await_count == 1
    assert message.answer.await_args.kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_present_image_menu_fallback_on_send_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aiogram.exceptions import TelegramBadRequest
    from platforms.image_menu_flow import present_image_model_menu

    class _Row:
        tariff = "smart"

    monkeypatch.setattr(
        "services.billing.free_tier_gates.is_free_user",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "platforms.image_menu_flow.get_user_row",
        AsyncMock(return_value=_Row()),
    )
    monkeypatch.setattr(
        "services.billing.daily_quotas.get_free_photo_snapshot",
        AsyncMock(return_value=MagicMock(used=0, day="2026-08-04")),
    )
    monkeypatch.setattr(
        "services.billing.daily_quotas.quota_day",
        lambda: "2026-08-04",
    )

    message = MagicMock()
    message.answer = AsyncMock(
        side_effect=[
            TelegramBadRequest(method=None, message="bad html"),
            TelegramBadRequest(method=None, message="bad plain"),
            None,
        ]
    )
    state = AsyncMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()

    await present_image_model_menu(message, state, user_id=999_002)

    assert message.answer.await_count == 3
    last_text = message.answer.await_args.args[0]
    assert "Не удалось открыть меню" in last_text
