"""Tests for 2-ref layout collage / photo booth mode."""

from __future__ import annotations

from services.photo_collage_mode import (
    build_collage_multi_ref_api_prompt,
    is_layout_collage_intent,
    resolve_collage_aspect_ratio,
)
from services.openrouter_images import (
    MULTI_REF_COLLAGE_PRIMARY_MODEL,
    OPENROUTER_GPT_IMAGE2_MODEL,
    resolve_multi_ref_collage_model_key,
)


PHOTO_BOOTH_PROMPT = (
    "Фотореалистичная чёрно-белая фотобудка в формате вертикальной сетки 2 колонки × 4 ряда. "
    "Слева — серия кадров с парой, справа — пустые кадры. "
    "Левая колонка: девушка крупным планом, смех, поцелуй, объятие. "
    "Правая колонка: парень крупным планом, затем обрезанные пустые кадры."
)


def test_is_layout_collage_intent_photo_booth() -> None:
    assert is_layout_collage_intent(PHOTO_BOOTH_PROMPT) is True
    assert is_layout_collage_intent("мама и дочка на пляже") is False


def test_resolve_collage_aspect_ratio_defaults_vertical() -> None:
    assert resolve_collage_aspect_ratio(PHOTO_BOOTH_PROMPT, None) == "9:16"
    assert resolve_collage_aspect_ratio(PHOTO_BOOTH_PROMPT, "1:1") == "9:16"


def test_resolve_collage_aspect_ratio_respects_user_choice() -> None:
    assert resolve_collage_aspect_ratio(PHOTO_BOOTH_PROMPT, "4:5") == "4:5"


def test_build_collage_prompt_lightweight_and_forbids_strangers() -> None:
    prompt = build_collage_multi_ref_api_prompt(PHOTO_BOOTH_PROMPT, 2)

    assert "USER COLLAGE BRIEF:" in prompt
    assert "vertical" not in prompt.lower() or "2 колонки" in prompt
    assert "фотобудка" in prompt
    assert "CRITICAL MULTI-SUBJECT IDENTITY DIRECTIVE" not in prompt
    assert "third persons" in prompt.lower() or "third person" in prompt.lower()
    assert "input_references[0]" in prompt
    assert "input_references[1]" in prompt
    assert "face printed on clothing" in prompt.lower() or "photo on t-shirt" in prompt.lower()


def test_build_collage_prompt_honors_explicit_photo_slots() -> None:
    user_prompt = f"фото1 = девушка, фото2 = парень. {PHOTO_BOOTH_PROMPT}"
    prompt = build_collage_multi_ref_api_prompt(user_prompt, 2)

    assert "девушка" in prompt
    assert "парень" in prompt
    assert "input_references[0]" in prompt
    assert "input_references[1]" in prompt


def test_collage_routes_to_gpt_image_2() -> None:
    assert resolve_multi_ref_collage_model_key("nano_banana_pro") == OPENROUTER_GPT_IMAGE2_MODEL
    assert MULTI_REF_COLLAGE_PRIMARY_MODEL == OPENROUTER_GPT_IMAGE2_MODEL
