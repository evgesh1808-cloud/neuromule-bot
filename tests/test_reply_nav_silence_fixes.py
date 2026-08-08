"""Regression: nav dispatch and video FREE block must not crash silently."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.use_cases.video_generation_turn import VideoGenOutcome, VideoGenResult


@pytest.mark.asyncio
async def test_reply_video_gen_result_free_premium_blocked() -> None:
    from platforms.telegram_utils import _reply_video_gen_result

    message = MagicMock()
    message.answer = AsyncMock()
    vr = VideoGenResult(outcome=VideoGenOutcome.FREE_PREMIUM_BLOCKED)

    with patch(
        "platforms.telegram_utils.send_free_create_blocked",
        new_callable=AsyncMock,
    ) as blocked:
        await _reply_video_gen_result(message, vr, None)
        blocked.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_try_dispatch_reply_nav_button_delegates() -> None:
    from platforms.telegram_utils import try_dispatch_reply_nav_button

    message = MagicMock()
    message.text = "🚀 Тарифы"
    state = MagicMock()

    with patch(
        "platforms.handlers.menu_support.dispatch_reply_nav_button",
        new_callable=AsyncMock,
        return_value=True,
    ) as dispatch:
        assert await try_dispatch_reply_nav_button(message, state) is True
        dispatch.assert_awaited_once_with(message, state)
