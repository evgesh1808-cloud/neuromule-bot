"""Фото+caption до выбора aspect ratio и image-document в photo-flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiogram.enums import ParseMode

from content import messages as msg
from platforms.telegram_states import UserFlow


@pytest.mark.asyncio
async def test_photo_with_caption_in_aspect_ratio_state_starts_i2i() -> None:
    from platforms.handlers import generation_fsm

    message = MagicMock()
    message.photo = [MagicMock(file_id="AgAC_ref")]
    message.caption = "make background blue"
    message.document = None
    message.from_user.id = 42
    message.chat.id = 42
    message.answer = AsyncMock()

    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={
            "image_model_id": "flux-schnell",
            "image_model_label": "Flux 2 Pro",
            "image_aspect_ratio": "1:1",
        }
    )
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    with patch.object(generation_fsm, "process_photo_prompt_message", new_callable=AsyncMock) as proc:
        handled = await generation_fsm._dispatch_photo_reference_message(message, state)

    assert handled is True
    proc.assert_awaited_once_with(
        message,
        state,
        model_id="flux-schnell",
        label="Flux 2 Pro",
        prompt="make background blue",
        telegram_file_id="AgAC_ref",
        aspect_ratio="1:1",
    )


@pytest.mark.asyncio
async def test_image_document_with_caption_in_waiting_for_photo() -> None:
    from platforms.handlers import generation_fsm

    message = MagicMock()
    message.photo = None
    message.document = MagicMock(
        file_id="BQAC_ref",
        mime_type="image/png",
        file_name="ref.png",
    )
    message.caption = "add sunset"
    message.from_user.id = 7
    message.chat.id = 7
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

    proc.assert_awaited_once()
    assert proc.await_args.kwargs["telegram_file_id"] == "BQAC_ref"
    assert proc.await_args.kwargs["prompt"] == "add sunset"
