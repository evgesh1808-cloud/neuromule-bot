"""Tests for Mish04-style verbatim group prompts and ref slot mapping."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from config import Settings
from services.group_ref_slot_map import (
    apply_explicit_ref_slots,
    build_ordered_role_slots,
    layout_from_ref_slots,
    merge_ref_slot_maps,
    parse_explicit_ref_slot_map,
    should_preserve_verbatim_group_prompt,
)
from services.multi_ref_scene_parser import SceneCharacter, SceneLayout, map_multi_ref_slots
from services.openrouter_images import (
    FACE_DESCRIBE_GROUP_EXTRA,
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


def test_apply_explicit_ref_slots_overrides_mapper_label() -> None:
    layout = SceneLayout(
        characters=[
            SceneCharacter(
                ref_index=2,
                label="first child",
                placement="left",
                appearance_anchor="girl child",
            )
        ],
        scene_description_en="scene",
    )
    updated = apply_explicit_ref_slots(layout, {2: "daughter"}, ["a", "b", "girl face", "d"])
    by_ref = {c.ref_index: c for c in updated.characters}
    assert by_ref[2].label == "daughter"


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
    assert "under-eye bags" in prompt


def test_build_structured_prompt_photo3_daughter_slot() -> None:
    user_prompt = "фото3 = дочка, все вместе на пляже"
    slots = parse_explicit_ref_slot_map(user_prompt)
    layout = layout_from_ref_slots(slots, ["a", "b", "c"], user_prompt)
    prompt = build_structured_multi_ref_prompt(layout, ["a", "b", "c"])

    assert "input_references[2]" in prompt
    assert "дочка" in prompt
    assert "NOT the mother/woman" in prompt


@pytest.mark.asyncio
async def test_map_multi_ref_slots_maps_daughter_by_face_not_upload_order() -> None:
    settings = Settings(tg_token="t", openrouter_key="k")
    llm_json = json.dumps(
        {
            "characters": [
                {"ref_index": 0, "label": "father", "placement": "as in user scene", "appearance_anchor": "adult male"},
                {"ref_index": 1, "label": "mother", "placement": "as in user scene", "appearance_anchor": "adult female"},
                {"ref_index": 3, "label": "daughter", "placement": "as in user scene", "appearance_anchor": "girl child"},
                {"ref_index": 2, "label": "son", "placement": "as in user scene", "appearance_anchor": "boy child"},
            ]
        }
    )
    faces = [
        "Adult male, strong jaw",
        "Adult female, soft eyes",
        "Boy child, round face",
        "Girl child, bright eyes",
    ]

    with patch(
        "services.ai_text.ask_ai_messages",
        AsyncMock(return_value=type("R", (), {"content": llm_json})()),
    ):
        layout = await map_multi_ref_slots(settings, MISH04_STYLE_PROMPT, faces)

    by_ref = {c.ref_index: c.label for c in layout.characters}
    assert by_ref[3] == "daughter"
    assert by_ref[1] == "mother"


@pytest.mark.asyncio
async def test_build_group_api_prompt_uses_face_mapper_and_verbatim_scene() -> None:
    settings = Settings(tg_token="t", openrouter_key="k")
    faces = ["dad face", "mom face", "daughter face", "son face"]
    mapped = SceneLayout(
        characters=[
            SceneCharacter(ref_index=0, label="father", placement="x", appearance_anchor="dad"),
            SceneCharacter(ref_index=1, label="mother", placement="x", appearance_anchor="mom"),
            SceneCharacter(ref_index=2, label="daughter", placement="x", appearance_anchor="daughter"),
            SceneCharacter(ref_index=3, label="son", placement="x", appearance_anchor="son"),
        ],
        scene_description_en=MISH04_STYLE_PROMPT,
    )

    with patch(
        "services.multi_ref_scene_parser.map_multi_ref_slots",
        AsyncMock(return_value=mapped),
    ) as mapper:
        prompt = await build_group_multi_ref_api_prompt(settings, MISH04_STYLE_PROMPT, faces)

    mapper.assert_awaited_once()
    assert "User scene prompt (preserve composition" in prompt
    assert "vertical matte wall" in prompt
    assert "input_references[2]" in prompt
    assert "daughter" in prompt.lower()


@pytest.mark.asyncio
async def test_build_group_api_prompt_short_prompt_still_verbatim() -> None:
    settings = Settings(tg_token="t", openrouter_key="k")
    faces = ["man", "woman"]
    mapped = SceneLayout(
        characters=[
            SceneCharacter(ref_index=0, label="husband", placement="left", appearance_anchor="man"),
            SceneCharacter(ref_index=1, label="wife", placement="right", appearance_anchor="woman"),
        ],
        scene_description_en="на пляже",
    )

    with patch(
        "services.multi_ref_scene_parser.map_multi_ref_slots",
        AsyncMock(return_value=mapped),
    ):
        prompt = await build_group_multi_ref_api_prompt(settings, "на пляже", faces)

    assert "User scene prompt (preserve composition" in prompt
    assert "на пляже" in prompt


def test_face_describe_group_extra_labels_child() -> None:
    assert "girl child" in FACE_DESCRIBE_GROUP_EXTRA


def test_infer_slots_from_face_descriptions_daughter_not_upload_order() -> None:
    from services.group_ref_slot_map import infer_slots_from_face_descriptions

    faces = [
        "Adult male, beard",
        "Adult female, long hair",
        "Boy child, round face",
        "Girl child, bright eyes, long brown hair",
    ]
    slots = infer_slots_from_face_descriptions(faces)
    assert slots[2] == "son"
    assert slots[3] == "daughter"


def test_reconcile_layout_relabels_first_child_as_daughter() -> None:
    from services.group_ref_slot_map import reconcile_layout_with_face_slots

    layout = SceneLayout(
        characters=[
            SceneCharacter(ref_index=2, label="first child", placement="x", appearance_anchor=""),
            SceneCharacter(ref_index=3, label="second child", placement="x", appearance_anchor=""),
        ],
        scene_description_en="scene",
    )
    faces = ["a", "b", "Boy child face", "Girl child with long hair"]
    updated = reconcile_layout_with_face_slots(layout, faces)
    by_ref = {c.ref_index: c.label for c in updated.characters}
    assert by_ref[2] == "son"
    assert by_ref[3] == "daughter"


def test_apply_vertical_peek_placements_family_stack() -> None:
    from services.group_ref_slot_map import apply_vertical_peek_placements

    layout = SceneLayout(
        characters=[
            SceneCharacter(ref_index=0, label="man/father", placement="x", appearance_anchor=""),
            SceneCharacter(ref_index=1, label="woman/mother", placement="x", appearance_anchor=""),
            SceneCharacter(ref_index=3, label="daughter", placement="x", appearance_anchor=""),
            SceneCharacter(ref_index=2, label="son", placement="x", appearance_anchor=""),
        ],
        scene_description_en="scene",
    )
    prompt = "подглядывают из-за вертикальной матовой стены слева"
    updated = apply_vertical_peek_placements(layout, prompt)
    by_ref = {c.ref_index: c.placement for c in updated.characters}
    assert "top of vertical peek stack" in by_ref[0]
    assert "position 3" in by_ref[3]
    assert "position 4" in by_ref[2] or "bottom" in by_ref[2].lower()
