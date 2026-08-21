"""Media group (альбом) и UX photo refine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_album_two_photos_with_caption_triggers_composite() -> None:
    from platforms.handlers import generation_fsm

    message = MagicMock()
    message.from_user.id = 501
    message.chat.id = 501
    message.message_id = 20
    message.photo = [MagicMock(file_id="AgAC_print")]
    message.caption = "надень принт на худи"
    message.document = None
    message.answer = AsyncMock()

    album = [
        MagicMock(
            message_id=19,
            photo=[MagicMock(file_id="AgAC_face")],
            caption=None,
            document=None,
        ),
        message,
    ]

    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={
            "image_model_id": "nano_banana_pro",
            "image_model_label": "Nano Pro",
            "image_aspect_ratio": "1:1",
        }
    )
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    with patch.object(
        generation_fsm,
        "process_photo_prompt_message",
        new_callable=AsyncMock,
    ) as proc:
        handled = await generation_fsm._dispatch_photo_album_message(message, state, album)

    assert handled is True
    proc.assert_awaited_once()
    assert proc.await_args.kwargs["composite_refine"] is True
    assert proc.await_args.kwargs["composite_base_file_id"] == "AgAC_face"
    assert proc.await_args.kwargs["telegram_file_id"] == "AgAC_print"
    assert proc.await_args.kwargs["prompt"] == "надень принт на худи"


@pytest.mark.asyncio
async def test_album_rejected_in_refine_mode() -> None:
    from platforms.handlers import generation_fsm

    message = MagicMock()
    message.from_user.id = 502
    message.chat.id = 502
    message.answer = AsyncMock()

    album = [
        MagicMock(message_id=1, photo=[MagicMock(file_id="A")], caption=None, document=None),
        MagicMock(message_id=2, photo=[MagicMock(file_id="B")], caption="x", document=None),
    ]

    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={
            "image_model_id": "nano_banana_pro",
            "image_model_label": "Nano Pro",
            "refine_from_result": True,
        }
    )

    handled = await generation_fsm._dispatch_photo_album_message(message, state, album)

    assert handled is True
    message.answer.assert_awaited_once()
    assert "альбом" in message.answer.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_album_pending_pair_plus_text_triggers_composite_not_refine() -> None:
    """Альбом без подписи → текст: не путаем с refine-сессией."""
    from platforms.handlers import generation_fsm

    message = MagicMock()
    message.text = "надень принт на футболку"
    message.from_user.id = 504
    message.chat.id = 504
    message.answer = AsyncMock()

    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={
            "image_model_id": "nano_banana_pro",
            "image_model_label": "Nano Pro",
            "image_aspect_ratio": "1:1",
            "pending_group_ref_file_ids": ["AgAC_face", "AgAC_print"],
            "pending_reference_file_id": "AgAC_face",
            "pending_object_file_id": "AgAC_print",
            "refine_from_result": False,
        }
    )
    state.update_data = AsyncMock()

    with patch.object(generation_fsm, "process_photo_prompt_message", new_callable=AsyncMock) as proc, patch(
        "services.photo_intent_parser.resolve_photo_edit_prompt",
        new_callable=AsyncMock,
        return_value=("1:1", "надень принт на футболку", False),
    ):
        await generation_fsm.photo_process(message, state)

    proc.assert_awaited_once()
    assert proc.await_args.kwargs["composite_refine"] is True
    assert proc.await_args.kwargs["composite_base_file_id"] == "AgAC_face"
    assert proc.await_args.kwargs["telegram_file_id"] == "AgAC_print"
    message.answer.assert_not_called()


@pytest.mark.asyncio
async def test_second_photo_without_caption_becomes_pending_object() -> None:
    from platforms.handlers import generation_fsm

    message = MagicMock()
    message.photo = [MagicMock(file_id="AgAC_print")]
    message.caption = None
    message.from_user.id = 503
    message.answer = AsyncMock()

    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={
            "image_model_id": "nano_banana_pro",
            "image_model_label": "Nano Pro",
            "pending_reference_file_id": "AgAC_face",
        }
    )
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    with patch.object(
        generation_fsm,
        "_photo_reference_from_message",
        return_value=("AgAC_print", ""),
    ):
        handled = await generation_fsm._dispatch_photo_reference_message(message, state)

    assert handled is True
    merged: dict = {}
    for call in state.update_data.await_args_list:
        merged.update(call.kwargs)
    assert merged["pending_group_ref_file_ids"] == ["AgAC_face", "AgAC_print"]
    assert merged["pending_object_file_id"] == "AgAC_print"


@pytest.mark.asyncio
async def test_photo_refine_restores_from_db_when_memory_expired() -> None:
    from platforms.handlers import generation_cb
    from services.photo_edit_session import reset_photo_edit_sessions_for_tests

    reset_photo_edit_sessions_for_tests()

    callback = MagicMock()
    callback.from_user.id = 777
    callback.message = MagicMock()
    callback.message.chat.id = 777
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()

    state = MagicMock()
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    persisted = {
        "telegram_file_id": "AgAC_db",
        "media_url": None,
        "image_model_id": "nano_banana_pro",
        "image_model_label": "Nano Pro",
        "aspect_ratio": "1:1",
        "user_prompt": "portrait",
    }

    with patch(
        "services.repository.get_last_generated_image",
        new_callable=AsyncMock,
        return_value=persisted,
    ):
        await generation_cb.photo_refine_start(callback, state)

    kwargs = state.update_data.await_args.kwargs
    assert kwargs["pending_reference_file_id"] is None
    assert kwargs["refine_from_result"] is True
    callback.message.answer.assert_awaited()
    assert "Режим доработки активирован" in callback.message.answer.await_args.args[0]
