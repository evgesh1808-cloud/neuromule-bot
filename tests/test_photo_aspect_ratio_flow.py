"""Aspect ratio FSM step и прокидка в OpenRouter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiogram.enums import ParseMode

from content import messages as msg
from platforms.telegram_states import UserFlow
from services.photo_aspect_ratio import aspect_ratio_from_callback_suffix, normalize_photo_aspect_ratio


def test_aspect_ratio_callback_decode() -> None:
    assert aspect_ratio_from_callback_suffix("1x1") == "1:1"
    assert aspect_ratio_from_callback_suffix("3x4") == "3:4"
    assert aspect_ratio_from_callback_suffix("16x9") == "16:9"
    assert aspect_ratio_from_callback_suffix("bad") is None


def test_normalize_aspect_ratio_defaults() -> None:
    assert normalize_photo_aspect_ratio(None) == "1:1"
    assert normalize_photo_aspect_ratio("16:9") == "16:9"
    assert normalize_photo_aspect_ratio("9:16") == "1:1"


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
    assert msg.TXT_PICK_ASPECT_RATIO in args[0]
    assert kwargs.get("parse_mode") == ParseMode.HTML


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
