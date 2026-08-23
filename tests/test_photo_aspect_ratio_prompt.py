"""Tests for prompt-inferred aspect ratio."""

from __future__ import annotations

from services.photo_aspect_ratio import resolve_prompt_aspect_ratio


FAMILY_PEEK_PROMPT = (
    "Соедини на одном фото всех людей, не меняя внешности. "
    "подглядывают из-за вертикальной матовой стены. формат 9:16"
)


def test_resolve_prompt_aspect_ratio_from_9_16_text() -> None:
    assert resolve_prompt_aspect_ratio(FAMILY_PEEK_PROMPT, None) == "9:16"
    assert resolve_prompt_aspect_ratio(FAMILY_PEEK_PROMPT, "1:1") == "9:16"


def test_resolve_prompt_aspect_ratio_respects_user_pick() -> None:
    assert resolve_prompt_aspect_ratio(FAMILY_PEEK_PROMPT, "4:5") == "4:5"
