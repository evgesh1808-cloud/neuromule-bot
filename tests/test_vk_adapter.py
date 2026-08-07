"""VK-адаптер → Core-роутер: нормализация photo URL."""

from __future__ import annotations

from unittest.mock import MagicMock

from platforms.vk_adapter import extract_vk_photo_url, normalize_vk_message, to_photo_generation_request


def test_extract_vk_photo_url_from_attachments() -> None:
    message = MagicMock()
    message.attachments = [
        MagicMock(
            type="photo",
            photo=MagicMock(
                sizes=[
                    MagicMock(width=130, height=87, url="https://cdn/s.jpg"),
                    MagicMock(width=1280, height=960, url="https://cdn/max.jpg"),
                ]
            ),
        )
    ]
    assert extract_vk_photo_url(message) == "https://cdn/max.jpg"


def test_normalize_vk_message_with_text_and_photo() -> None:
    message = MagicMock()
    message.from_id = 1001
    message.peer_id = 1001
    message.text = "make sky purple"
    message.attachments = [
        MagicMock(
            type="photo",
            photo=MagicMock(
                sizes=[MagicMock(width=800, height=600, url="https://cdn/ref.jpg")]
            ),
        )
    ]

    inbound = normalize_vk_message(message)
    assert inbound.user_id == 1001
    assert inbound.text == "make sky purple"
    assert inbound.reference_image_url == "https://cdn/ref.jpg"


def test_to_photo_generation_request_builds_core_payload() -> None:
    inbound = normalize_vk_message(
        MagicMock(
            from_id=42,
            peer_id=42,
            text="prompt",
            attachments=[],
        )
    )
    req = to_photo_generation_request(
        inbound,
        image_model_id="nano_banana2",
        image_model_label="Nano Banana 2",
        prompt="prompt",
        reference_image_url="https://cdn/ref.jpg",
    )
    assert req.platform == "vk"
    assert req.photo_ref is not None
    assert req.photo_ref.reference_image_url == "https://cdn/ref.jpg"
    assert req.photo_ref.telegram_file_id is None
