"""Кнопка «Оживить это фото» под результатом генерации."""

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
    return message


@pytest.mark.asyncio
async def test_result_animate_uses_photo_from_message() -> None:
    callback = MagicMock()
    callback.from_user.id = 7001
    callback.message = _photo_message()
    callback.answer = AsyncMock()
    bot = MagicMock()

    with (
        patch.object(handlers.deps, "bot", return_value=bot),
        patch.object(
            handlers,
            "run_animate_generation_turn",
            AsyncMock(return_value=AnimateGenResult(outcome=AnimateGenOutcome.SUCCESS)),
        ) as turn,
    ):
        await handlers.result_animate_photo(callback)

    callback.answer.assert_awaited_once_with()
    turn.assert_awaited_once_with(
        uid=7001,
        telegram_file_id="AgAC_photo",
        bot=bot,
        chat_id=7001,
        settings=handlers.settings,
    )


@pytest.mark.asyncio
async def test_result_animate_without_photo_shows_expired_alert() -> None:
    callback = MagicMock()
    callback.from_user.id = 7002
    callback.message = MagicMock()
    callback.message.chat.id = 7002
    callback.message.photo = None
    callback.message.document = None
    callback.answer = AsyncMock()

    with patch.object(handlers, "get_photo_edit_session", return_value=None):
        await handlers.result_animate_photo(callback)

    callback.answer.assert_awaited_once_with(msg.TXT_PHOTO_REFINE_EXPIRED, show_alert=True)


@pytest.mark.asyncio
async def test_result_animate_not_stub_in_payment_misc() -> None:
    from platforms.handlers import payment_misc

    assert msg.CB_RESULT_ANIMATE not in payment_misc.result_cbs
