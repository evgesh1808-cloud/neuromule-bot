"""Payload image-to-image: текст отдельно, картинка только как base64 inline_data."""

from __future__ import annotations

import base64

import pytest

from services.free_image_cascade import _build_user_content
from services.gemini_image_client import build_gemini_generate_content_body


def test_gemini_payload_keeps_text_and_base64_inline_data() -> None:
    raw = b"\xff\xd8\xff" + b"JPEGDATA"
    body = build_gemini_generate_content_body(
        "сделай портрет",
        reference_image_bytes=raw,
        reference_mime="image/jpeg",
    )
    parts = body["contents"][0]["parts"]
    assert parts[0] == {"text": "сделай портрет"}
    assert "inline_data" in parts[1]
    assert parts[1]["inline_data"]["mime_type"] == "image/jpeg"
    assert parts[1]["inline_data"]["data"] == base64.b64encode(raw).decode("ascii")
    # Сырые байты не утекли в text.
    assert isinstance(parts[0]["text"], str)
    assert b"\xff\xd8" not in parts[0]["text"].encode("utf-8", errors="surrogateescape")


def test_gemini_rejects_bytes_as_prompt() -> None:
    with pytest.raises(RuntimeError, match="must be str"):
        build_gemini_generate_content_body(b"not-a-prompt")


def test_openrouter_content_separates_text_and_image_url() -> None:
    raw = b"PNGDATA"
    content = _build_user_content(
        "улучши фото",
        reference_image_bytes=raw,
        reference_mime="image/png",
    )
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "улучши фото"}
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert url.endswith(base64.b64encode(raw).decode("ascii"))
