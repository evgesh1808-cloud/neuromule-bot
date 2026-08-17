"""Tests for album routing: composite print vs group multi-ref."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.photo_multi_ref_routing import (
    is_composite_print_intent,
    should_route_album_as_composite,
    should_route_album_as_group,
)


def test_is_composite_print_intent_detects_clothing_keywords() -> None:
    assert is_composite_print_intent("надень принт на худи")
    assert is_composite_print_intent("put logo on t-shirt")
    assert is_composite_print_intent("перенеси маленькую меня на футболку как принт")
    assert is_composite_print_intent("Добавь с фотографии где я маленькая принт на футболке")
    assert is_composite_print_intent("покажи второе фото как отражение в зеркале")
    assert not is_composite_print_intent("все вместе на фоне заката")


def test_should_route_album_as_group_for_portraits() -> None:
    assert should_route_album_as_group(num_refs=2, prompt="семейное фото вместе")
    assert should_route_album_as_group(num_refs=5, prompt="все стоят в ряд")
    assert not should_route_album_as_group(num_refs=2, prompt="надень принт на футболку")
    assert not should_route_album_as_group(num_refs=2, prompt="")


def test_should_route_album_as_composite_only_for_print() -> None:
    assert should_route_album_as_composite(num_refs=2, prompt="надень принт на худи")
    assert not should_route_album_as_composite(num_refs=2, prompt="вместе на пляже")
    assert not should_route_album_as_composite(num_refs=3, prompt="надень принт")


@pytest.mark.asyncio
async def test_album_two_portraits_routes_to_group_multi_ref() -> None:
    from platforms.handlers import generation_fsm

    message = MagicMock()
    message.from_user.id = 601
    message.chat.id = 601
    message.message_id = 22
    message.photo = [MagicMock(file_id="AgAC_p2")]
    message.caption = "все вместе на фоне заката"
    message.document = None
    message.answer = AsyncMock()

    album = [
        MagicMock(
            message_id=21,
            photo=[MagicMock(file_id="AgAC_p1")],
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
    assert proc.await_args.kwargs["group_multi_ref"] is True
    assert proc.await_args.kwargs["group_ref_file_ids"] == ["AgAC_p1", "AgAC_p2"]
    assert proc.await_args.kwargs.get("composite_refine") is not True


@pytest.mark.asyncio
async def test_pending_pair_non_print_text_routes_to_group() -> None:
    from platforms.handlers import generation_fsm

    message = MagicMock()
    message.text = "все вместе улыбаются в камеру"
    message.from_user.id = 602
    message.chat.id = 602
    message.answer = AsyncMock()

    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={
            "image_model_id": "nano_banana_pro",
            "image_model_label": "Nano Pro",
            "image_aspect_ratio": "1:1",
            "pending_reference_file_id": "AgAC_p1",
            "pending_object_file_id": "AgAC_p2",
            "refine_from_result": False,
        }
    )
    state.update_data = AsyncMock()

    with patch.object(generation_fsm, "process_photo_prompt_message", new_callable=AsyncMock) as proc, patch(
        "services.photo_intent_parser.resolve_photo_edit_prompt",
        new_callable=AsyncMock,
        return_value=("1:1", "все вместе улыбаются в камеру", False),
    ):
        await generation_fsm.photo_process(message, state)

    proc.assert_awaited_once()
    assert proc.await_args.kwargs["group_multi_ref"] is True
    assert proc.await_args.kwargs["group_ref_file_ids"] == ["AgAC_p1", "AgAC_p2"]
    message.answer.assert_not_called()
