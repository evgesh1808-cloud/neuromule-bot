"""process_photo_prompt_message не падает на normalize_image_prompt_text."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.use_cases.photo_generation_turn import PhotoGenOutcome, PhotoGenResult


@pytest.mark.asyncio
async def test_process_photo_prompt_sends_status_without_name_error() -> None:
    from platforms.handlers.generation_fsm import process_photo_prompt_message

    status = MagicMock()
    status.message_id = 999
    status.edit_text = AsyncMock()
    status.delete = AsyncMock()

    message = MagicMock()
    message.from_user.id = 42
    message.chat.id = 100
    message.answer = AsyncMock(return_value=status)

    state = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    state.set_state = AsyncMock()
    state.clear = AsyncMock()

    studio_prompt = (
        "Генерация логотипа студии (Формат 1:1):A powerful esports gaming logo, "
        "1:1, cyberpunk, masterpiece, 8k"
    )

    mock_bot = MagicMock()
    with (
        patch("platforms.handlers.generation_fsm.deps.bot", return_value=mock_bot),
        patch(
            "platforms.handlers.generation_fsm.run_photo_generation_turn",
            new=AsyncMock(
                return_value=PhotoGenResult(
                    outcome=PhotoGenOutcome.DAILY_LIMIT_EXCEEDED,
                )
            ),
        ),
        patch(
            "platforms.handlers.generation_fsm.chat_action_loop",
            new=lambda *_a, **_k: _AsyncNullContext(),
        ),
    ):
        await process_photo_prompt_message(
            message,
            state,
            model_id="free_photo",
            label="Flux FREE",
            prompt=studio_prompt,
        )

    message.answer.assert_awaited()


class _AsyncNullContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None
