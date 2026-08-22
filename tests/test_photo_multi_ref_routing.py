"""Tests for album routing: composite (merch) vs group multi-ref (2+ photos)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.photo_multi_ref_routing import (
    is_composite_merch_intent,
    is_composite_print_intent,
    should_route_album_as_composite,
    should_route_as_group_multi_ref,
)


def test_is_composite_merch_intent_keywords() -> None:
    assert is_composite_merch_intent("надень принт на худи")
    assert is_composite_merch_intent("логотип на футболке")
    assert is_composite_merch_intent("перенеси на одежду")
    assert is_composite_merch_intent("сделай мерч с этим фото")
    assert is_composite_print_intent("принт на футболке")
    assert not is_composite_merch_intent("мама и дочка на пляже")
    assert not is_composite_merch_intent("покажи второе фото как отражение в зеркале")


def test_should_route_as_group_for_two_plus_photos_by_default() -> None:
    assert should_route_as_group_multi_ref(num_refs=2, prompt="мама и дочка на пляже")
    assert should_route_as_group_multi_ref(num_refs=5, prompt="семейный портрет")
    assert not should_route_as_group_multi_ref(num_refs=1, prompt="cat")
    assert not should_route_as_group_multi_ref(
        num_refs=2,
        prompt="принт на футболке",
    )


def test_should_route_album_as_composite_only_merch_two_photos() -> None:
    assert should_route_album_as_composite(num_refs=2, prompt="надень принт на худи")
    assert not should_route_album_as_composite(num_refs=2, prompt="вместе на пляже")
    assert not should_route_album_as_composite(num_refs=3, prompt="принт на футболке")


@pytest.mark.asyncio
async def test_album_two_portraits_routes_to_group_multi_ref() -> None:
    from platforms.handlers import generation_fsm

    message = MagicMock()
    message.from_user.id = 601
    message.chat.id = 601
    message.message_id = 22
    message.photo = [MagicMock(file_id="AgAC_p2")]
    message.caption = "мама и дочка на пляже"
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
    assert proc.await_args.kwargs["prompt"] == "мама и дочка на пляже"


@pytest.mark.asyncio
async def test_pending_refs_plus_text_routes_to_group_without_keywords() -> None:
    from platforms.handlers import generation_fsm

    message = MagicMock()
    message.text = "мама и дочка улыбаются"
    message.from_user.id = 602
    message.chat.id = 602
    message.answer = AsyncMock()

    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={
            "image_model_id": "nano_banana_pro",
            "image_model_label": "Nano Pro",
            "image_aspect_ratio": "1:1",
            "pending_group_ref_file_ids": ["AgAC_p1", "AgAC_p2"],
            "pending_reference_file_id": "AgAC_p1",
            "pending_object_file_id": "AgAC_p2",
            "refine_from_result": False,
        }
    )
    state.update_data = AsyncMock()

    with patch.object(generation_fsm, "process_photo_prompt_message", new_callable=AsyncMock) as proc, patch(
        "services.photo_intent_parser.resolve_photo_edit_prompt",
        new_callable=AsyncMock,
        return_value=("1:1", "мама и дочка улыбаются", False),
    ):
        await generation_fsm.photo_process(message, state)

    proc.assert_awaited_once()
    assert proc.await_args.kwargs["group_multi_ref"] is True
    assert proc.await_args.kwargs["group_ref_file_ids"] == ["AgAC_p1", "AgAC_p2"]
    message.answer.assert_not_called()


@pytest.mark.asyncio
async def test_second_photo_with_caption_routes_to_group_not_composite() -> None:
    """Фото1 без текста → фото2 с промптом: group multi-ref, не composite."""
    from platforms.handlers import generation_fsm

    message = MagicMock()
    message.from_user.id = 603
    message.chat.id = 603
    message.message_id = 31
    message.photo = [MagicMock(file_id="AgAC_p2")]
    message.caption = "мама и дочка на пляже"
    message.document = None
    message.answer = AsyncMock()

    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={
            "image_model_id": "nano_banana_pro",
            "image_model_label": "Nano Pro",
            "image_aspect_ratio": "1:1",
            "pending_group_ref_file_ids": ["AgAC_p1"],
            "pending_reference_file_id": "AgAC_p1",
            "refine_from_result": False,
        }
    )
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    with patch.object(
        generation_fsm,
        "process_photo_prompt_message",
        new_callable=AsyncMock,
    ) as proc:
        handled = await generation_fsm._dispatch_photo_reference_message(message, state)

    assert handled is True
    proc.assert_awaited_once()
    assert proc.await_args.kwargs["group_multi_ref"] is True
    assert proc.await_args.kwargs["group_ref_file_ids"] == ["AgAC_p1", "AgAC_p2"]
    assert proc.await_args.kwargs.get("composite_refine") is not True


@pytest.mark.asyncio
async def test_second_photo_with_merch_caption_routes_to_composite() -> None:
    from platforms.handlers import generation_fsm

    message = MagicMock()
    message.from_user.id = 604
    message.chat.id = 604
    message.message_id = 41
    message.photo = [MagicMock(file_id="AgAC_print")]
    message.caption = "надень принт на футболку"
    message.document = None
    message.answer = AsyncMock()

    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={
            "image_model_id": "nano_banana_pro",
            "image_model_label": "Nano Pro",
            "image_aspect_ratio": "1:1",
            "pending_group_ref_file_ids": ["AgAC_face"],
            "pending_reference_file_id": "AgAC_face",
            "refine_from_result": False,
        }
    )
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    with patch.object(
        generation_fsm,
        "process_photo_prompt_message",
        new_callable=AsyncMock,
    ) as proc:
        handled = await generation_fsm._dispatch_photo_reference_message(message, state)

    assert handled is True
    proc.assert_awaited_once()
    assert proc.await_args.kwargs["composite_refine"] is True
    assert proc.await_args.kwargs["composite_base_file_id"] == "AgAC_face"
    assert proc.await_args.kwargs["telegram_file_id"] == "AgAC_print"

