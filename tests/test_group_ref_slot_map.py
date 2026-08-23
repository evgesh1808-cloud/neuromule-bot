"""Tests for Mish04-style verbatim group prompts and ref slot mapping."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from config import Settings
from services.group_ref_slot_map import (
    build_ordered_role_slots,
    layout_from_ref_slots,
    merge_ref_slot_maps,
    parse_explicit_ref_slot_map,
    should_preserve_verbatim_group_prompt,
)
from services.multi_ref_scene_parser import SceneCharacter, SceneLayout
from services.openrouter_images import (
    build_group_multi_ref_api_prompt,
    build_structured_multi_ref_prompt,
)


MISH04_STYLE_PROMPT = (
    "A family of four peeking from behind a vertical matte wall, 9:16 portrait. "
    "Мужчина в черной футболке, женщина в черной майке, первый ребенок в черной "
    "футболке, второй ребенок в черной футболке. "
    "Не меняя черты лица ни на 1%, сохранить идентичность каждого input_references. "
    + ("детали композиции и света. " * 40)
)


def test_should_preserve_verbatim_long_prompt() -> None:
    assert should_preserve_verbatim_group_prompt(MISH04_STYLE_PROMPT) is True


def test_should_preserve_verbatim_identity_markers() -> None:
    assert should_preserve_verbatim_group_prompt("не меняя черты лица") is True
    assert should_preserve_verbatim_group_prompt("короткий промпт") is False


def test_parse_explicit_ref_slot_map_photo_equals() -> None:
    slots = parse_explicit_ref_slot_map("фото1 = папа, фото3 = дочка")
    assert slots == {0: "папа", 2: "дочка"}


def test_parse_explicit_ref_slot_map_input_references() -> None:
    slots = parse_explicit_ref_slot_map("input_references[1] = мама, input_references[3] = сын")
    assert slots == {1: "мама", 3: "сын"}


def test_build_ordered_role_slots_family_of_four() -> None:
    prompt = (
        "Мужчина в черной футболке, женщина в черной майке, "
        "первый ребенок в черной футболке, второй ребенок в черной футболке"
    )
    slots = build_ordered_role_slots(prompt, 4)
    assert slots[0] == "man/father"
    assert slots[1] == "woman/mother"
    assert slots[2] == "first child"
    assert slots[3] == "second child"


def test_merge_ref_slot_maps_explicit_overrides_ordered() -> None:
    ordered = {0: "man/father", 1: "woman/mother"}
    explicit = {2: "daughter"}
    merged = merge_ref_slot_maps(ordered, explicit)
    assert merged[0] == "man/father"
    assert merged[2] == "daughter"


def test_build_structured_prompt_verbatim_preserves_user_text() -> None:
    face_descriptions = ["dad", "mom", "daughter", "son"]
    layout = layout_from_ref_slots(
        {0: "man/father", 1: "woman/mother", 2: "daughter", 3: "son"},
        face_descriptions,
        MISH04_STYLE_PROMPT,
    )
    prompt = build_structured_multi_ref_prompt(
        layout,
        face_descriptions,
        verbatim_scene_text=MISH04_STYLE_PROMPT,
    )

    assert "User scene prompt (preserve composition" in prompt
    assert "vertical matte wall" in prompt
    assert "input_references[2]" in prompt
    assert "daughter" in prompt.lower()
    assert "NOT the mother/woman" in prompt
    assert "Scene and cinematic composition:" not in prompt


def test_build_structured_prompt_photo3_daughter_slot() -> None:
    user_prompt = "фото3 = дочка, все вместе на пляже"
    slots = parse_explicit_ref_slot_map(user_prompt)
    layout = layout_from_ref_slots(slots, ["a", "b", "c"], user_prompt)
    prompt = build_structured_multi_ref_prompt(layout, ["a", "b", "c"])

    assert "input_references[2]" in prompt
    assert "дочка" in prompt
    assert "NOT the mother/woman" in prompt


@pytest.mark.asyncio
async def test_build_group_api_prompt_skips_scene_director_for_verbatim() -> None:
    settings = Settings(tg_token="t", openrouter_key="k")
    faces = ["dad face", "mom face", "daughter face", "son face"]

    with patch(
        "services.multi_ref_scene_parser.parse_multi_ref_scene",
        AsyncMock(side_effect=AssertionError("director must be skipped")),
    ):
        prompt = await build_group_multi_ref_api_prompt(settings, MISH04_STYLE_PROMPT, faces)

    assert "vertical matte wall" in prompt
    assert "input_references[2]" in prompt


@pytest.mark.asyncio
async def test_build_group_api_prompt_uses_director_for_short_unmapped() -> None:
    settings = Settings(tg_token="t", openrouter_key="k")
    faces = ["man", "woman"]

    with patch(
        "services.multi_ref_scene_parser.parse_multi_ref_scene",
        AsyncMock(
            return_value=SceneLayout(
                characters=[
                    SceneCharacter(
                        ref_index=0,
                        label="husband",
                        placement="left",
                        appearance_anchor="man",
                    ),
                    SceneCharacter(
                        ref_index=1,
                        label="wife",
                        placement="right",
                        appearance_anchor="woman",
                    ),
                ],
                scene_description_en="Couple on beach",
            )
        ),
    ) as director:
        prompt = await build_group_multi_ref_api_prompt(settings, "на пляже", faces)

    director.assert_awaited_once()
    assert "Couple on beach" in prompt
