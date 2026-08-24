"""FSM «Оживить фото» — отправка фото пользователем."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from platforms.handlers import generation_fsm as handlers
from services.use_cases.animate_generation_turn import AnimateGenOutcome, AnimateGenResult


@pytest.mark.asyncio
async def test_animate_photo_process_uses_deps_bot() -> None:
    message = MagicMock()
    message.from_user.id = 5010
    message.chat.id = 5010
    message.photo = [SimpleNamespace(file_id="small"), SimpleNamespace(file_id="AgAC_anim")]
    message.answer = AsyncMock()

    state = MagicMock()
    mock_bot = MagicMock()

    with (
        patch.object(handlers.deps, "bot", return_value=mock_bot),
        patch.object(
            handlers,
            "run_animate_generation_turn",
            AsyncMock(return_value=AnimateGenResult(outcome=AnimateGenOutcome.SUCCESS)),
        ) as turn,
    ):
        await handlers.animate_photo_process(message, state)

    turn.assert_awaited_once()
    assert turn.await_args.kwargs["bot"] is mock_bot
    assert turn.await_args.kwargs["telegram_file_id"] == "AgAC_anim"
