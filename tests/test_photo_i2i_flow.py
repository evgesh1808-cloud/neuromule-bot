"""Двухшаговый i2i: фото → промпт в waiting_for_photo."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiogram.enums import ParseMode

from content import messages as msg


@pytest.mark.asyncio
async def test_photo_without_caption_waits_for_prompt() -> None:
    from platforms.handlers import generation_fsm

    message = MagicMock()
    message.photo = [MagicMock(file_id="AgAC_ref")]
    message.caption = None
    message.from_user.id = 42
    message.answer = AsyncMock()

    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={
            "image_model_id": "nano_banana2",
            "image_model_label": "Nano Banana 2",
        }
    )
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    with patch.object(generation_fsm, "process_photo_prompt_message", new_callable=AsyncMock) as proc:
        await generation_fsm.photo_process_with_image(message, state)

    proc.assert_not_called()
    state.update_data.assert_awaited_once()
    kwargs = state.update_data.await_args.kwargs
    assert kwargs["pending_reference_file_id"] == "AgAC_ref"
    assert kwargs["photo_service_message_ids"]
    message.answer.assert_awaited_once_with(
        msg.TXT_CREATE_IMAGE_WAIT_PROMPT,
        parse_mode=ParseMode.HTML,
    )


@pytest.mark.asyncio
async def test_text_after_pending_photo_passes_file_id() -> None:
    from platforms.handlers import generation_fsm

    message = MagicMock()
    message.text = "make sky purple"
    message.from_user.id = 42

    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={
            "image_model_id": "flux-schnell",
            "image_model_label": "Flux 2 Pro",
            "pending_reference_file_id": "AgAC_ref",
        }
    )
    state.update_data = AsyncMock()

    with patch.object(generation_fsm, "process_photo_prompt_message", new_callable=AsyncMock) as proc:
        await generation_fsm.photo_process(message, state)

    state.update_data.assert_awaited_once_with(
        pending_reference_file_id=None,
        refine_from_result=None,
    )
    proc.assert_awaited_once_with(
        message,
        state,
        model_id="flux-schnell",
        label="Flux 2 Pro",
        prompt="make sky purple",
        telegram_file_id="AgAC_ref",
        reference_image_url=None,
        reference_image_bytes=None,
        reference_mime="image/jpeg",
        aspect_ratio="1:1",
    )


@pytest.mark.asyncio
async def test_text_only_still_triggers_t2i() -> None:
    from platforms.handlers import generation_fsm

    message = MagicMock()
    message.text = "red apple on table"
    message.from_user.id = 42

    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={
            "image_model_id": "nano_banana2",
            "image_model_label": "Nano Banana 2",
        }
    )

    with patch.object(generation_fsm, "process_photo_prompt_message", new_callable=AsyncMock) as proc:
        await generation_fsm.photo_process(message, state)

    proc.assert_awaited_once_with(
        message,
        state,
        model_id="nano_banana2",
        label="Nano Banana 2",
        prompt="red apple on table",
        aspect_ratio="1:1",
    )
