"""Multi-turn edit session (15 мин) и reply-to-photo."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content import messages as msg
from services.photo_edit_session import (
    get_photo_edit_session,
    reset_photo_edit_sessions_for_tests,
    save_photo_edit_session,
)


@pytest.fixture(autouse=True)
def _clear_sessions() -> None:
    reset_photo_edit_sessions_for_tests()
    yield
    reset_photo_edit_sessions_for_tests()


def test_edit_session_ttl() -> None:
    save_photo_edit_session(
        100,
        image_model_id="flux_schnell",
        image_model_label="Flux 2 Pro",
        aspect_ratio="3:4",
        telegram_file_id="AgAC_test",
        message_id=555,
        chat_id=100,
        ttl_sec=0.05,
    )
    assert get_photo_edit_session(100) is not None
    time.sleep(0.06)
    assert get_photo_edit_session(100) is None


@pytest.mark.asyncio
async def test_photo_refine_callback_prefers_generated_result_over_selfie() -> None:
    from platforms.handlers import generation_cb

    save_photo_edit_session(
        42,
        image_model_id="flux_schnell",
        image_model_label="Flux 2 Pro",
        aspect_ratio="1:1",
        telegram_file_id="AgAC_result",
        reference_file_id="AgAC_selfie",
    )

    callback = MagicMock()
    callback.from_user.id = 42
    callback.message = MagicMock()
    callback.message.chat.id = 42
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()

    state = MagicMock()
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    await generation_cb.photo_refine_start(callback, state)

    kwargs = state.update_data.await_args.kwargs
    assert kwargs["pending_reference_file_id"] is None
    assert kwargs["refine_from_result"] is True


@pytest.mark.asyncio
async def test_photo_refine_callback_sets_pending_reference() -> None:
    from platforms.handlers import generation_cb

    save_photo_edit_session(
        42,
        image_model_id="flux_schnell",
        image_model_label="Flux 2 Pro",
        aspect_ratio="1:1",
        telegram_file_id="AgAC_refine",
    )

    callback = MagicMock()
    callback.from_user.id = 42
    callback.message = MagicMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()

    state = MagicMock()
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    await generation_cb.photo_refine_start(callback, state)

    state.update_data.assert_awaited()
    kwargs = state.update_data.await_args.kwargs
    assert kwargs["pending_reference_file_id"] is None
    assert kwargs["pending_object_file_id"] is None
    assert kwargs["refine_from_result"] is True
    assert kwargs["image_model_id"] == "flux_schnell"
    callback.message.answer.assert_awaited()


@pytest.mark.asyncio
async def test_reply_to_bot_photo_starts_i2i() -> None:
    from platforms.handlers import generation_fsm

    save_photo_edit_session(
        77,
        image_model_id="nano_banana2",
        image_model_label="Nano Banana 2",
        aspect_ratio="16:9",
        telegram_file_id="AgAC_old",
    )

    bot_user = MagicMock()
    bot_user.id = 999001

    reply_photo = MagicMock()
    reply_photo.file_id = "AgAC_reply"

    reply_msg = MagicMock()
    reply_msg.photo = [reply_photo]
    reply_msg.from_user = bot_user

    message = MagicMock()
    message.text = "add golden hour lighting"
    message.from_user.id = 77
    message.reply_to_message = reply_msg

    state = MagicMock()
    state.get_data = AsyncMock(return_value={})
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    with patch.object(
        generation_fsm.deps,
        "bot",
        return_value=MagicMock(id=999001),
    ), patch.object(
        generation_fsm,
        "process_photo_prompt_message",
        new_callable=AsyncMock,
    ) as proc, patch(
        "services.photo_intent_parser.parse_image_intent",
        new_callable=AsyncMock,
        return_value=(None, "add golden hour lighting"),
    ):
        handled = await generation_fsm.try_start_photo_edit_from_reply(message, state)

    assert handled is True
    proc.assert_awaited_once()
    assert proc.await_args.kwargs["telegram_file_id"] == "AgAC_reply"
    assert proc.await_args.kwargs["aspect_ratio"] == "16:9"


@pytest.mark.asyncio
async def test_reply_to_bot_photo_updates_aspect_from_intent() -> None:
    from platforms.handlers import generation_fsm

    save_photo_edit_session(
        88,
        image_model_id="flux_schnell",
        image_model_label="Flux 2 Pro",
        aspect_ratio="1:1",
        telegram_file_id="AgAC_old",
    )

    bot_user = MagicMock()
    bot_user.id = 999001

    reply_photo = MagicMock()
    reply_photo.file_id = "AgAC_reply"

    reply_msg = MagicMock()
    reply_msg.photo = [reply_photo]
    reply_msg.from_user = bot_user

    message = MagicMock()
    message.text = "сделай stories 9:16, добавь неон"
    message.from_user.id = 88
    message.reply_to_message = reply_msg

    state = MagicMock()
    state.get_data = AsyncMock(return_value={})
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    with patch.object(
        generation_fsm.deps,
        "bot",
        return_value=MagicMock(id=999001),
    ), patch.object(
        generation_fsm,
        "process_photo_prompt_message",
        new_callable=AsyncMock,
    ) as proc, patch(
        "services.photo_intent_parser.parse_image_intent",
        new_callable=AsyncMock,
        return_value=("9:16", "добавь неон"),
    ):
        handled = await generation_fsm.try_start_photo_edit_from_reply(message, state)

    assert handled is True
    assert proc.await_args.kwargs["aspect_ratio"] == "9:16"
    assert proc.await_args.kwargs["prompt"] == "добавь неон"
    sess = get_photo_edit_session(88)
    assert sess is not None
    assert sess.aspect_ratio == "9:16"


@pytest.mark.asyncio
async def test_save_last_generated_image_roundtrip(repo_module, tmp_path) -> None:
    from services.repository import get_last_generated_image, save_last_generated_image

    user_id = 4242
    await save_last_generated_image(
        user_id,
        telegram_file_id="AgAC_persist",
        media_url="https://cdn.example/x.jpg",
        image_model_id="nano_banana_pro",
        image_model_label="Nano Pro",
        aspect_ratio="3:4",
        user_prompt="test prompt",
    )
    row = await get_last_generated_image(user_id)
    assert row is not None
    assert row["telegram_file_id"] == "AgAC_persist"
    assert row["image_model_id"] == "nano_banana_pro"
    assert row["aspect_ratio"] == "3:4"


@pytest.mark.asyncio
async def test_get_or_restore_photo_edit_session_from_db() -> None:
    from services.photo_edit_session import (
        get_or_restore_photo_edit_session,
        reset_photo_edit_sessions_for_tests,
    )

    reset_photo_edit_sessions_for_tests()

    persisted = {
        "telegram_file_id": "AgAC_anchor",
        "media_url": None,
        "image_model_id": "flux_schnell",
        "image_model_label": "Flux 2 Pro",
        "aspect_ratio": "16:9",
        "user_prompt": "scene",
    }

    with patch(
        "services.repository.get_last_generated_image",
        new_callable=AsyncMock,
        return_value=persisted,
    ):
        sess = await get_or_restore_photo_edit_session(900, peer_id=900)

    assert sess is not None
    assert sess.telegram_file_id == "AgAC_anchor"
    assert sess.image_model_id == "flux_schnell"
    assert sess.aspect_ratio == "16:9"


def test_result_keyboard_has_refine_button() -> None:
    from content.inline_keyboards import result_photo_keyboard

    kb = result_photo_keyboard()
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert msg.BTN_PHOTO_REFINE in texts
