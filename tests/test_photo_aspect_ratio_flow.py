"""Aspect ratio FSM step и прокидка в OpenRouter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiogram.enums import ParseMode

from content import messages as msg
from platforms.telegram_states import UserFlow
from services.photo_aspect_ratio import (
    ASPECT_RATIO_MENU_ENTRIES,
    aspect_ratio_from_callback_suffix,
    format_aspect_ratio_picker_html,
    model_shows_aspect_ratio_menu,
    normalize_photo_aspect_ratio,
    openrouter_aspect_ratio,
)


def test_aspect_ratio_callback_decode() -> None:
    assert aspect_ratio_from_callback_suffix("1x1") == "1:1"
    assert aspect_ratio_from_callback_suffix("3x4") == "3:4"
    assert aspect_ratio_from_callback_suffix("4x5") == "4:5"
    assert aspect_ratio_from_callback_suffix("9x16") == "9:16"
    assert aspect_ratio_from_callback_suffix("16x9") == "16:9"
    assert aspect_ratio_from_callback_suffix("bad") is None


def test_aspect_ratio_menu_entries_map_to_openrouter_values() -> None:
    for entry in ASPECT_RATIO_MENU_ENTRIES:
        assert entry.value in {"1:1", "3:4", "4:5", "9:16", "16:9"}
        assert aspect_ratio_from_callback_suffix(entry.callback_suffix) == entry.value
        assert openrouter_aspect_ratio(entry.value) == entry.value
        assert len(entry.button_label) <= 8


def test_format_aspect_ratio_picker_html_lists_all_ratios() -> None:
    text = format_aspect_ratio_picker_html()
    assert "▢ 1:1" in text
    assert "📱 9:16" in text
    assert "Instagram" in text


def test_normalize_aspect_ratio_defaults() -> None:
    assert normalize_photo_aspect_ratio(None) == "1:1"
    assert normalize_photo_aspect_ratio("16:9") == "16:9"
    assert normalize_photo_aspect_ratio("9:16") == "9:16"
    assert normalize_photo_aspect_ratio("4:5") == "4:5"
    assert normalize_photo_aspect_ratio("9:21") == "1:1"


def test_model_shows_aspect_ratio_menu() -> None:
    assert model_shows_aspect_ratio_menu("flux-schnell") is True
    assert model_shows_aspect_ratio_menu("flux_schnell") is True
    assert model_shows_aspect_ratio_menu("nano_banana_pro") is True
    assert model_shows_aspect_ratio_menu("nano_banana2") is False
    assert model_shows_aspect_ratio_menu("dalle_3") is False
    assert model_shows_aspect_ratio_menu("gpt_image2") is False


@pytest.mark.asyncio
async def test_pick_image_model_goes_to_aspect_ratio_state() -> None:
    from platforms.handlers import generation_cb

    callback = MagicMock()
    callback.from_user.id = 7
    callback.data = f"{msg.CB_IMG_PREFIX}flux-schnell"
    callback.message = MagicMock()
    callback.message.edit_reply_markup = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()

    state = MagicMock()
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    with patch(
        "services.billing.free_tier_gates.is_free_user",
        new_callable=AsyncMock,
        return_value=False,
    ), patch(
        "services.billing.free_tier_gates.free_allows_image_model",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "platforms.image_menu_flow.clear_image_model_menu_pending",
        new_callable=AsyncMock,
    ):
        await generation_cb.pick_image_model(callback, state)

    state.set_state.assert_awaited_with(UserFlow.waiting_for_image_aspect_ratio)
    callback.message.answer.assert_awaited()
    args, kwargs = callback.message.answer.await_args
    assert "▢ 1:1" in args[0]
    assert "📱 9:16" in args[0]
    assert kwargs.get("parse_mode") == ParseMode.HTML


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "callback_model",
    ["nano_banana2", "dalle_3", "gpt_image2"],
)
async def test_pick_image_model_skips_aspect_menu(callback_model: str) -> None:
    from platforms.handlers import generation_cb

    callback = MagicMock()
    callback.from_user.id = 8
    callback.data = f"{msg.CB_IMG_PREFIX}{callback_model}"
    callback.message = MagicMock()
    callback.message.edit_reply_markup = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()

    state = MagicMock()
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    with patch(
        "services.billing.free_tier_gates.is_free_user",
        new_callable=AsyncMock,
        return_value=False,
    ), patch(
        "services.billing.free_tier_gates.free_allows_image_model",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "platforms.image_menu_flow.clear_image_model_menu_pending",
        new_callable=AsyncMock,
    ):
        await generation_cb.pick_image_model(callback, state)

    state.set_state.assert_awaited_with(UserFlow.waiting_for_photo)
    state.update_data.assert_awaited()
    _, kwargs = state.update_data.await_args
    assert kwargs.get("image_aspect_ratio") == "1:1"
    callback.message.answer.assert_awaited()
    args, _kwargs = callback.message.answer.await_args
    assert msg.TXT_CREATE_IMAGE_AFTER_MODEL in args[0]
    assert "reply_markup" not in _kwargs or _kwargs.get("reply_markup") is None


@pytest.mark.asyncio
async def test_pick_aspect_ratio_sets_fsm_and_prompts() -> None:
    from platforms.handlers import generation_cb

    callback = MagicMock()
    callback.from_user.id = 9
    callback.data = f"{msg.CB_IMG_AR_PREFIX}3x4"
    callback.message = MagicMock()
    callback.message.edit_reply_markup = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()

    state = MagicMock()
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    await generation_cb.pick_image_aspect_ratio(callback, state)

    state.update_data.assert_awaited_with(image_aspect_ratio="3:4")
    state.set_state.assert_awaited_with(UserFlow.waiting_for_photo)
    callback.message.answer.assert_awaited_with(msg.TXT_CREATE_IMAGE_AFTER_MODEL)


@pytest.mark.asyncio
async def test_photo_enqueue_spec_carries_aspect_ratio() -> None:
    from services.use_cases.photo_generation_turn import PhotoGenOutcome, run_photo_generation_turn

    spend = MagicMock()
    spend.ok = True
    spend.charge = MagicMock(
        used_photo_free_slot=False,
        crystals=3,
        charge_id="c1",
    )

    with patch(
        "services.use_cases.photo_generation_turn.billing.spend_image_resource",
        new_callable=AsyncMock,
        return_value=spend,
    ), patch(
        "services.repository.get_user_row",
        new_callable=AsyncMock,
        return_value=MagicMock(tariff="smart"),
    ):
        result = await run_photo_generation_turn(
            MagicMock(),
            MagicMock(),
            1,
            42,
            "flux_schnell",
            "Flux 2 Pro",
            "sunset beach",
            aspect_ratio="16:9",
        )

    assert result.outcome is PhotoGenOutcome.SUCCESS
    assert result.enqueue is not None
    assert result.enqueue.aspect_ratio == "16:9"
