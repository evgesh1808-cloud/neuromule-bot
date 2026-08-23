"""Tests for 2-ref layout collage / photo booth mode."""

from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

from services.photo_collage_mode import (
    build_collage_multi_ref_api_prompt,
    compose_collage_reference_sheet,
    is_layout_collage_intent,
    resolve_collage_aspect_ratio,
    resolve_collage_openrouter_extensions,
    resolve_collage_ref_order,
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


def _tiny_png_data_url(color: tuple[int, int, int]) -> str:
    img = Image.new("RGB", (64, 80), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.standard_b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def test_is_layout_collage_intent_photo_booth() -> None:
    assert is_layout_collage_intent(PHOTO_BOOTH_PROMPT) is True
    assert is_layout_collage_intent("мама и дочка на пляже") is False


def test_resolve_collage_aspect_ratio_defaults_vertical() -> None:
    assert resolve_collage_aspect_ratio(PHOTO_BOOTH_PROMPT, None) == "9:16"
    assert resolve_collage_aspect_ratio(PHOTO_BOOTH_PROMPT, "1:1") == "9:16"


def test_resolve_collage_aspect_ratio_respects_user_choice() -> None:
    assert resolve_collage_aspect_ratio(PHOTO_BOOTH_PROMPT, "4:5") == "4:5"


def test_build_collage_composite_prompt_uses_left_right_sheet() -> None:
    prompt = build_collage_multi_ref_api_prompt(
        PHOTO_BOOTH_PROMPT,
        2,
        composite_sheet=True,
        left_role="девушка",
        right_role="парень",
    )

    assert "LEFT half = девушка" in prompt
    assert "RIGHT half = парень" in prompt
    assert "side-by-side identity sheet" in prompt
    assert "input_references[1]" not in prompt


def test_build_collage_prompt_honors_explicit_photo_slots() -> None:
    user_prompt = f"фото1 = девушка, фото2 = парень. {PHOTO_BOOTH_PROMPT}"
    prompt = build_collage_multi_ref_api_prompt(
        user_prompt,
        2,
        composite_sheet=True,
        left_role="девушка",
        right_role="парень",
    )

    assert "девушка" in prompt
    assert "парень" in prompt


def test_compose_collage_reference_sheet_is_wider_than_single_face() -> None:
    left = _tiny_png_data_url((200, 100, 100))
    right = _tiny_png_data_url((100, 100, 200))
    sheet = compose_collage_reference_sheet(left, right)
    raw = base64.b64decode(sheet.split(",", 1)[1])
    with Image.open(BytesIO(raw)) as img:
        assert img.width > 64
        assert img.height >= 64


def test_resolve_collage_ref_order_puts_female_left() -> None:
    prompt = "фото1 = парень, фото2 = девушка. фотобудка коллаж"
    left_idx, right_idx = resolve_collage_ref_order(prompt, 2)
    assert left_idx == 1
    assert right_idx == 0


def test_resolve_collage_openrouter_extensions_quality_high() -> None:
    assert resolve_collage_openrouter_extensions(OPENROUTER_GPT_IMAGE2_MODEL) == {"quality": "high"}


def test_collage_routes_to_gpt_image_2() -> None:
    assert resolve_multi_ref_collage_model_key("nano_banana_pro") == OPENROUTER_GPT_IMAGE2_MODEL
    assert MULTI_REF_COLLAGE_PRIMARY_MODEL == OPENROUTER_GPT_IMAGE2_MODEL
