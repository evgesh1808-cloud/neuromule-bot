"""Кнопка «Оживить» — прямой запуск без FSM-опроса."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content import messages as msg
from platforms.handlers import photo_result_callbacks as handlers
from services.use_cases.animate_generation_turn import AnimateGenOutcome, AnimateGenResult


def _photo_message(*, file_id: str = "AgAC_photo") -> MagicMock:
    message = MagicMock()
    message.chat.id = 7001
    message.photo = [SimpleNamespace(file_id="small"), SimpleNamespace(file_id=file_id)]
    message.document = None
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_result_animate_runs_generation_turn_directly() -> None:
    callback = MagicMock()
    callback.from_user.id = 7001
    callback.message = _photo_message()
    callback.answer = AsyncMock()

    state = MagicMock()

    mock_bot = MagicMock()
    with (
        patch.object(handlers.deps, "bot", return_value=mock_bot),
        patch.object(handlers, "_resolve_animate_file_id", AsyncMock(return_value="AgAC_photo")),
        patch.object(
            handlers,
            "run_animate_generation_turn",
            AsyncMock(return_value=AnimateGenResult(outcome=AnimateGenOutcome.SUCCESS)),
        ) as run_turn,
    ):
        await handlers.result_animate_photo(callback, state)

    callback.answer.assert_awaited_once_with()
    run_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_result_animate_shows_busy_when_lock_active() -> None:
    callback = MagicMock()
    callback.from_user.id = 7001
    callback.message = _photo_message()
    callback.answer = AsyncMock()

    state = MagicMock()

    with (
        patch.object(handlers.deps, "bot", return_value=MagicMock()),
        patch.object(handlers, "_resolve_animate_file_id", AsyncMock(return_value="AgAC_photo")),
        patch.object(
            handlers,
            "run_animate_generation_turn",
            AsyncMock(
                return_value=AnimateGenResult(outcome=AnimateGenOutcome.ALREADY_GENERATING)
            ),
        ),
    ):
        await handlers.result_animate_photo(callback, state)

    callback.message.answer.assert_awaited()
    assert msg.TXT_ANIMATE_GENERATING_BUSY in callback.message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_result_animate_regenerate_uses_last_source() -> None:
    callback = MagicMock()
    callback.from_user.id = 7001
    callback.message = _photo_message()
    callback.answer = AsyncMock()

    last = SimpleNamespace(source_file_id="AgAC_source", motion_prompt=None)

    with (
        patch.object(handlers.deps, "bot", return_value=MagicMock()),
        patch("services.last_animate_request.get", return_value=last),
        patch.object(
            handlers,
            "run_animate_generation_turn",
            AsyncMock(return_value=AnimateGenResult(outcome=AnimateGenOutcome.SUCCESS)),
        ) as run_turn,
    ):
        await handlers.result_animate_regenerate(callback)

    callback.answer.assert_awaited_once_with(msg.TXT_ANIMATE_REGENERATE_STARTED)
    run_turn.assert_awaited_once()
    assert run_turn.await_args.kwargs["telegram_file_id"] == "AgAC_source"
