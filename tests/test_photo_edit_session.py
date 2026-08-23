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



@pytest.mark.asyncio
async def test_sharpen_refine_uses_upscale_api() -> None:
    from platforms.handlers import generation_fsm
    from services.photo_edit_session import save_photo_edit_session

    save_photo_edit_session(
        88,
        image_model_id="flux_schnell",
        image_model_label="Flux 2 Pro",
        aspect_ratio="1:1",
        telegram_file_id="AgAC_result",
        user_prompt="portrait",
    )

    message = MagicMock()
    message.text = "сделать четче"
    message.from_user.id = 88
    message.chat.id = 88
    message.answer = AsyncMock()

    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={
            "image_model_id": "flux_schnell",
            "image_model_label": "Flux 2 Pro",
            "image_aspect_ratio": "1:1",
            "refine_from_result": True,
        }
    )
    state.update_data = AsyncMock()

    bot = MagicMock()
    bot.send_photo = AsyncMock(
        return_value=MagicMock(photo=[MagicMock(file_id="AgAC_upscaled")])
    )

    with (
        patch.object(generation_fsm.deps, "bot", return_value=bot),
        patch.object(generation_fsm.billing, "spend_upscale", AsyncMock(return_value=MagicMock(ok=True, charge=MagicMock(charge_id="c1")))),
        patch(
            "services.openrouter_images.resolve_openrouter_reference_url",
            AsyncMock(return_value="https://cdn.example/base.png"),
        ),
        patch(
            "services.openrouter_images.upscale_openrouter_image_url",
            AsyncMock(return_value="https://cdn.example/upscaled.png"),
        ) as upscale,
        patch(
            "services.photo_edit_session.persist_photo_edit_session",
            AsyncMock(),
        ),
        patch(
            "services.photo_intent_parser.resolve_photo_edit_prompt",
            AsyncMock(return_value=("1:1", "сделать четче", False)),
        ),
        patch.object(generation_fsm, "process_photo_prompt_message", AsyncMock()) as proc,
    ):
        await generation_fsm.photo_process(message, state)

    upscale.assert_awaited_once()
    proc.assert_not_called()


def test_result_keyboard_has_refine_button() -> None:
    from content.inline_keyboards import result_photo_keyboard

    kb = result_photo_keyboard()
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert msg.BTN_PHOTO_REFINE in texts


@pytest.mark.asyncio
async def test_group_refine_uses_result_image_not_group_multi_ref() -> None:
    from platforms.handlers import generation_fsm
    from services.photo_edit_session import save_photo_edit_session

    save_photo_edit_session(
        55,
        image_model_id="nano_banana_pro",
        image_model_label="Nano Banana Pro",
        aspect_ratio="9:16",
        telegram_file_id="AgAC_group_result",
        user_prompt="family peek scene",
        group_ref_file_ids=("ref0", "ref1", "ref2", "ref3"),
        group_base_prompt="family peek scene",
    )

    message = MagicMock()
    message.text = "сделай стену светлее"
    message.from_user.id = 55
    message.chat.id = 55
    message.answer = AsyncMock()

    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={
            "image_model_id": "nano_banana_pro",
            "image_model_label": "Nano Banana Pro",
            "image_aspect_ratio": "9:16",
            "refine_from_result": True,
        }
    )
    state.update_data = AsyncMock()

    with patch.object(
        generation_fsm,
        "process_photo_prompt_message",
        new_callable=AsyncMock,
    ) as proc, patch(
        "services.photo_intent_parser.resolve_photo_edit_prompt",
        new_callable=AsyncMock,
        return_value=("9:16", "сделай стену светлее", False),
    ):
        await generation_fsm.photo_process(message, state)

    proc.assert_awaited_once()
    kwargs = proc.await_args.kwargs
    assert kwargs["telegram_file_id"] == "AgAC_group_result"
    assert kwargs["i2i_reference_mode"] == "edit"
    assert kwargs.get("group_multi_ref") is not True
    assert "EDIT REQUEST" in kwargs["prompt"]
