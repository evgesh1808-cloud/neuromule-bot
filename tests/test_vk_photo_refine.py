"""VK multi-turn refine: callback payload, peer pending, edit-session gating."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content import messages as msg
from platforms.vk_photo_keyboard import parse_vk_refine_payload, vk_photo_refine_keyboard_json
from services.photo_edit_session import reset_photo_edit_sessions_for_tests, save_photo_edit_session
from services.photo_aspect_ratio import openrouter_aspect_ratio, replicate_flux_aspect_ratio
from services.vk_refine_pending import mark_vk_refine_pending, peek_vk_refine_pending, reset_vk_refine_pending_for_tests


@pytest.fixture(autouse=True)
def _reset_stores() -> None:
    reset_photo_edit_sessions_for_tests()
    reset_vk_refine_pending_for_tests()
    yield
    reset_photo_edit_sessions_for_tests()
    reset_vk_refine_pending_for_tests()


def test_vk_refine_keyboard_payload() -> None:
    raw = vk_photo_refine_keyboard_json()
    assert msg.BTN_PHOTO_REFINE in raw
    assert msg.CB_PHOTO_REFINE in raw
    assert parse_vk_refine_payload('{"cmd":"photo_refine"}')


def test_aspect_ratio_provider_formats() -> None:
    assert openrouter_aspect_ratio("16x9") == "1:1"
    assert openrouter_aspect_ratio("16:9") == "16:9"
    assert openrouter_aspect_ratio("9:16") == "9:16"
    assert openrouter_aspect_ratio("4:5") == "4:5"
    assert replicate_flux_aspect_ratio("3:4") == "3:4"


@pytest.mark.asyncio
async def test_vk_text_without_refine_pending_is_t2i_not_i2i() -> None:
    from platforms import vk_photo_flow

    save_photo_edit_session(
        42,
        image_model_id="flux_schnell",
        image_model_label="Flux",
        aspect_ratio="1:1",
        reference_image_bytes=b"\xff\xd8\xff",
        platform="vk",
        chat_id=100,
    )
    vk_photo_flow.enter_vk_image_mode(100)
    vk_photo_flow._vk_image_model[100] = ("flux_schnell", "Flux", "1:1")

    message = MagicMock()
    message.text = "brand new scene without cat"
    message.from_id = 42
    message.peer_id = 100
    message.attachments = []

    with patch.object(vk_photo_flow, "route_photo_generation", new_callable=AsyncMock) as route:
        from services.use_cases.photo_generation_turn import PhotoGenOutcome, PhotoGenResult

        route.return_value = PhotoGenResult(outcome=PhotoGenOutcome.NEED_PROMPT)
        with patch.object(vk_photo_flow, "vk_answer", new_callable=AsyncMock):
            handled = await vk_photo_flow.handle_vk_photo_message(message)

    assert handled is True
    req = route.await_args.args[1]
    assert req.photo_ref is None


@pytest.mark.asyncio
async def test_vk_refine_pending_uses_edit_session_bytes() -> None:
    from platforms import vk_photo_flow

    save_photo_edit_session(
        42,
        image_model_id="flux_schnell",
        image_model_label="Flux 2 Pro",
        aspect_ratio="16:9",
        reference_image_bytes=b"\xff\xd8\xff",
        platform="vk",
        chat_id=100,
    )
    vk_photo_flow.enter_vk_image_mode(100)
    mark_vk_refine_pending(100, 42)

    message = MagicMock()
    message.text = "add sunset glow"
    message.from_id = 42
    message.peer_id = 100
    message.attachments = []

    with patch.object(vk_photo_flow, "route_photo_generation", new_callable=AsyncMock) as route:
        from services.use_cases.photo_generation_turn import PhotoGenOutcome, PhotoGenResult

        route.return_value = PhotoGenResult(outcome=PhotoGenOutcome.NEED_PROMPT)
        with patch.object(vk_photo_flow, "vk_answer", new_callable=AsyncMock):
            await vk_photo_flow.handle_vk_photo_message(message)

    req = route.await_args.args[1]
    assert req.photo_ref is not None
    assert req.photo_ref.reference_image_bytes == b"\xff\xd8\xff"
    assert req.aspect_ratio == "16:9"
    assert peek_vk_refine_pending(100) == 42


@pytest.mark.asyncio
async def test_vk_refine_pending_updates_aspect_from_intent() -> None:
    from platforms import vk_photo_flow

    save_photo_edit_session(
        42,
        image_model_id="flux_schnell",
        image_model_label="Flux 2 Pro",
        aspect_ratio="1:1",
        reference_image_bytes=b"\xff\xd8\xff",
        platform="vk",
        chat_id=100,
    )
    vk_photo_flow.enter_vk_image_mode(100)
    mark_vk_refine_pending(100, 42)

    message = MagicMock()
    message.text = "сделай широкий 16:9, добавь дождь"
    message.from_id = 42
    message.peer_id = 100
    message.attachments = []

    with patch.object(vk_photo_flow, "route_photo_generation", new_callable=AsyncMock) as route:
        from services.use_cases.photo_generation_turn import PhotoGenOutcome, PhotoGenResult

        route.return_value = PhotoGenResult(outcome=PhotoGenOutcome.NEED_PROMPT)
        with patch.object(vk_photo_flow, "vk_answer", new_callable=AsyncMock), patch(
            "services.photo_intent_parser.parse_image_intent",
            new_callable=AsyncMock,
            return_value=("16:9", "добавь дождь"),
        ):
            await vk_photo_flow.handle_vk_photo_message(message)

    req = route.await_args.args[1]
    assert req.aspect_ratio == "16:9"
    assert req.prompt == "добавь дождь"
    assert vk_photo_flow._vk_image_model[100][2] == "16:9"


@pytest.mark.asyncio
async def test_activate_vk_refine_rejects_wrong_peer() -> None:
    from platforms import vk_photo_flow

    save_photo_edit_session(
        42,
        image_model_id="flux_schnell",
        image_model_label="Flux 2 Pro",
        aspect_ratio="1:1",
        reference_image_bytes=b"\xff\xd8\xff",
        platform="vk",
        chat_id=100,
    )

    message = MagicMock()
    message.answer = AsyncMock()
    ok = await vk_photo_flow.activate_vk_photo_refine(peer_id=200, user_id=42, message=message)
    assert ok is False
