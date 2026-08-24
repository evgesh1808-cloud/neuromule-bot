"""Кнопка «Оживить» запускает FSM, а не немедленный job."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content import messages as msg
from platforms.handlers import photo_result_callbacks as handlers


def _photo_message(*, file_id: str = "AgAC_photo") -> MagicMock:
    message = MagicMock()
    message.chat.id = 7001
    message.photo = [SimpleNamespace(file_id="small"), SimpleNamespace(file_id=file_id)]
    message.document = None
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_result_animate_starts_motion_fsm() -> None:
    callback = MagicMock()
    callback.from_user.id = 7001
    callback.message = _photo_message()
    callback.answer = AsyncMock()

    state = MagicMock()
    state.clear = AsyncMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    state.get_data = AsyncMock(return_value={"animate_people": [{"ref_index": 0, "display_label": "Папа", "role_key": "папа"}]})

    with (
        patch.object(handlers, "_load_result_session", AsyncMock(return_value=None)),
        patch(
            "platforms.handlers.animate_motion_fsm.start_animate_motion_survey",
            AsyncMock(return_value=None),
        ) as start,
        patch(
            "platforms.handlers.animate_motion_fsm.send_animate_survey_intro",
            AsyncMock(),
        ) as intro,
        patch(
            "platforms.handlers.animate_motion_fsm.ask_first_animate_motion_step",
            AsyncMock(),
        ) as first_step,
    ):
        await handlers.result_animate_photo(callback, state)

    callback.answer.assert_awaited_once_with()
    start.assert_awaited_once()
    intro.assert_awaited_once()
    first_step.assert_awaited_once()


@pytest.mark.asyncio
async def test_result_animate_shows_busy_when_lock_active() -> None:
    from services.use_cases.animate_generation_turn import AnimateGenOutcome

    callback = MagicMock()
    callback.from_user.id = 7001
    callback.message = _photo_message()
    callback.answer = AsyncMock()

    state = MagicMock()

    with (
        patch.object(handlers, "_load_result_session", AsyncMock(return_value=None)),
        patch(
            "platforms.handlers.animate_motion_fsm.start_animate_motion_survey",
            AsyncMock(return_value=AnimateGenOutcome.ALREADY_GENERATING),
        ),
    ):
        await handlers.result_animate_photo(callback, state)

    callback.message.answer.assert_awaited()
    assert msg.TXT_ANIMATE_GENERATING_BUSY in callback.message.answer.await_args.args[0]
