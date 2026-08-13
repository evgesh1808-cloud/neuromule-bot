"""Тесты upscale x2/x4 под результатом фото."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content import messages as msg
from platforms.handlers import photo_result_callbacks as handlers
from services.photo_edit_session import save_photo_edit_session


@pytest.mark.asyncio
async def test_upscale_x2_insufficient_shows_alert() -> None:
    callback = MagicMock()
    callback.from_user.id = 9001
    callback.message.chat.id = 9001
    callback.answer = AsyncMock()

    save_photo_edit_session(
        9001,
        image_model_id="flux_schnell",
        image_model_label="Flux",
        media_url="https://fal.media/base.png",
        user_prompt="test",
    )

    with (
        patch.object(handlers, "openrouter_images_configured", return_value=True),
        patch.object(
            handlers,
            "get_user_row",
            AsyncMock(return_value=SimpleNamespace(crystals=0)),
        ),
        patch.object(handlers, "try_consume_crystals", AsyncMock()) as spend,
    ):
        await handlers.result_upscale_x2(callback)

    callback.answer.assert_awaited_once_with(msg.TXT_UPSCALE_X2_NEED_CRYSTAL, show_alert=True)
    spend.assert_not_awaited()


@pytest.mark.asyncio
async def test_upscale_x2_charges_before_openrouter_and_sends_document() -> None:
    callback = MagicMock()
    callback.from_user.id = 9002
    callback.message.chat.id = 9002
    callback.answer = AsyncMock()

    save_photo_edit_session(
        9002,
        image_model_id="flux_schnell",
        image_model_label="Flux",
        media_url="https://fal.media/base.png",
        user_prompt="portrait",
    )

    bot = MagicMock()
    bot.send_document = AsyncMock()

    with (
        patch.object(handlers, "openrouter_images_configured", return_value=True),
        patch.object(
            handlers,
            "get_user_row",
            AsyncMock(return_value=SimpleNamespace(crystals=5)),
        ),
        patch.object(handlers, "try_consume_crystals", AsyncMock(return_value=True)) as spend,
        patch.object(
            handlers,
            "upscale_openrouter_image_url",
            AsyncMock(return_value="https://cdn.openrouter.ai/upscaled-x2.png"),
        ) as upscale,
        patch.object(handlers.deps, "bot", return_value=bot),
        patch.object(handlers, "chat_action_loop", lambda *a, **kw: _noop_ctx()),
    ):
        await handlers.result_upscale_x2(callback)

    spend.assert_awaited_once_with(9002, 1)
    upscale.assert_awaited_once()
    assert upscale.await_args.kwargs["scale_value"] == 2
    bot.send_document.assert_awaited_once()
    assert bot.send_document.await_args.kwargs["document"] == "https://cdn.openrouter.ai/upscaled-x2.png"


@pytest.mark.asyncio
async def test_upscale_x4_needs_three_crystals() -> None:
    callback = MagicMock()
    callback.from_user.id = 9003
    callback.message.chat.id = 9003
    callback.answer = AsyncMock()

    save_photo_edit_session(
        9003,
        image_model_id="flux_schnell",
        image_model_label="Flux",
        media_url="https://fal.media/base.png",
    )

    with (
        patch.object(handlers, "openrouter_images_configured", return_value=True),
        patch.object(
            handlers,
            "get_user_row",
            AsyncMock(return_value=SimpleNamespace(crystals=2)),
        ),
        patch.object(handlers, "try_consume_crystals", AsyncMock()) as spend,
    ):
        await handlers.result_upscale_x4(callback)

    callback.answer.assert_awaited_once_with(msg.TXT_UPSCALE_X4_NEED_CRYSTAL, show_alert=True)
    spend.assert_not_awaited()


class _noop_ctx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False
