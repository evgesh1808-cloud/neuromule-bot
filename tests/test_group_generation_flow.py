"""Tests for multi-reference group photo payload, scene parser, and billing turn."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest

from config import Settings
from services.multi_ref_scene_parser import (
    SceneCharacter,
    SceneLayout,
    parse_multi_ref_scene,
    strip_json_markdown_fence,
)
from services.openrouter_images import (
    FACE_DESCRIBE_SYSTEM_PROMPT,
    OPENROUTER_FLUX_PAID_MODEL,
    OPENROUTER_NANO_BANANA2_MODEL,
    OPENROUTER_NANO_BANANA_PRO_MODEL,
    SELFIE_I2I_PROMPT_TEMPLATE,
    build_multi_ref_group_payload,
    build_structured_multi_ref_prompt,
    generate_openrouter_multi_ref_group_photo,
    resolve_multi_ref_group_fallbacks,
    resolve_multi_ref_group_model_key,
)
from services.use_cases.photo_generation_turn import PhotoGenOutcome, run_photo_generation_turn


def test_face_describe_prompt_forbids_numeric_age() -> None:
    assert "NEVER output specific age numbers" in FACE_DESCRIBE_SYSTEM_PROMPT
    assert "fresh, healthy, smooth, and well-rested appearance" in FACE_DESCRIBE_SYSTEM_PROMPT


def test_selfie_i2i_template_has_editorial_beauty_triggers() -> None:
    assert "high-end fashion editorial photography" in SELFIE_I2I_PROMPT_TEMPLATE.lower()
    assert "flawless smooth glowing skin" in SELFIE_I2I_PROMPT_TEMPLATE.lower()
    assert "professional retouching look" in SELFIE_I2I_PROMPT_TEMPLATE.lower()


def test_strip_json_markdown_fence() -> None:
    raw = '```json\n{"a": 1}\n```'
    assert strip_json_markdown_fence(raw) == '{"a": 1}'


def test_build_multi_ref_group_payload_passes_prompt_unchanged() -> None:
    scene = "мама и дочка на пляже, мягкий свет"
    with pytest.raises(Exception):
        build_multi_ref_group_payload(scene, ["https://example.com/a.png"])
    payload = build_multi_ref_group_payload(
        scene,
        ["https://example.com/a.png", "https://example.com/b.png"],
    )
    assert payload["prompt"] == scene
    assert len(payload["input_references"]) == 2
    assert "CRITICAL MULTI-SUBJECT" not in payload["prompt"]


def test_build_structured_multi_ref_prompt_husband_wife_unordered_album() -> None:
    layout = SceneLayout(
        characters=[
            SceneCharacter(
                ref_index=1,
                label="wife",
                placement="right side, leaning on husband's shoulder",
                appearance_anchor="woman with soft brown eyes and wavy dark hair",
            ),
            SceneCharacter(
                ref_index=0,
                label="husband",
                placement="left foreground, holding vintage photo album",
                appearance_anchor="man with strong jawline and short beard",
            ),
        ],
        scene_description_en=(
            "Couple on a sofa reviewing an unordered family photo album, warm window light"
        ),
    )
    face_descriptions = [
        "Adult man, strong jawline, short beard, fresh healthy skin",
        "Adult woman, soft brown eyes, wavy dark hair, smooth well-rested appearance",
    ]
    prompt = build_structured_multi_ref_prompt(layout, face_descriptions)

    assert "input_references[0]" in prompt
    assert "input_references[1]" in prompt
    assert "husband" in prompt.lower()
    assert "wife" in prompt.lower()
    assert "left foreground" in prompt
    assert "right side" in prompt
    assert "STRICTLY FORBIDDEN to swap or blend" in prompt
    assert "Do not age, rejuvenate" in prompt
    assert "photo album" in prompt.lower()


def test_build_structured_multi_ref_prompt_past_present_self() -> None:
    layout = SceneLayout(
        characters=[
            SceneCharacter(
                ref_index=0,
                label="present self",
                placement="center foreground, adult portrait",
                appearance_anchor="same person as reference, current appearance",
            ),
            SceneCharacter(
                ref_index=1,
                label="past self",
                placement="as a printed childhood photo held in hands",
                appearance_anchor="younger version of the same person on photo print",
            ),
        ],
        scene_description_en="Adult holding an old childhood photo of themselves, soft studio light",
    )
    face_descriptions = [
        "Middle-aged woman, oval face, calm expression, healthy smooth skin",
        "Young girl, same bone structure, bright eyes, childhood photo",
    ]
    prompt = build_structured_multi_ref_prompt(layout, face_descriptions)

    assert "present self" in prompt.lower()
    assert "past self" in prompt.lower()
    assert "input_references[0]" in prompt
    assert "input_references[1]" in prompt
    assert "printed childhood photo" in prompt
    assert "Preserve each subject's apparent age exactly" in prompt


@pytest.mark.asyncio
async def test_parse_multi_ref_scene_maps_roles_from_face_descriptions() -> None:
    settings = Settings(tg_token="t", openrouter_key="k")
    llm_json = json.dumps(
        {
            "characters": [
                {
                    "ref_index": 1,
                    "label": "wife",
                    "placement": "right",
                    "appearance_anchor": "woman with wavy hair",
                },
                {
                    "ref_index": 0,
                    "label": "husband",
                    "placement": "left",
                    "appearance_anchor": "man with beard",
                },
            ],
            "scene_description_en": "Couple on a couch with a photo album",
        }
    )

    with patch(
        "services.ai_text.ask_ai_messages",
        AsyncMock(return_value=SimpleNamespace(content=f"```json\n{llm_json}\n```")),
    ):
        layout = await parse_multi_ref_scene(
            settings,
            "на диване с фотоальбомом, муж слева, жена справа",
            ["man with beard", "woman with wavy hair"],
        )

    assert layout.characters[0].ref_index == 1
    assert layout.characters[0].label == "wife"
    assert layout.characters[1].ref_index == 0
    assert layout.characters[1].label == "husband"
    assert "photo album" in layout.scene_description_en.lower()


@pytest.mark.asyncio
async def test_generate_multi_ref_group_uses_prebuilt_structured_prompt() -> None:
    settings = Settings(tg_token="t", openrouter_key="k")
    refs = ["data:image/png;base64,aaa", "data:image/png;base64,bbb"]
    structured = "CRITICAL MULTI-SUBJECT structured prompt with input_references[0]"

    with (
        patch(
            "services.openrouter_images.ensure_png_reference_data_url",
            AsyncMock(side_effect=lambda url: url),
        ),
        patch(
            "services.openrouter_images.build_multi_banana_prompt_from_ru",
            AsyncMock(side_effect=AssertionError("should not build legacy prompt")),
        ),
        patch(
            "services.openrouter_images.generate_openrouter_image",
            AsyncMock(return_value=type("R", (), {"url": "https://cdn/out.png", "data": None})()),
        ) as gen_image,
    ):
        await generate_openrouter_multi_ref_group_photo(
            settings,
            model=OPENROUTER_NANO_BANANA_PRO_MODEL,
            user_prompt="мама и дочка на пляже",
            reference_image_data_urls=refs,
            api_prompt=structured,
        )

    assert "CRITICAL MULTI-SUBJECT" in gen_image.await_args.kwargs["prompt"]


def test_resolve_multi_ref_group_model_key_hard_routes_nano_pro() -> None:
    assert resolve_multi_ref_group_model_key("nano_banana_pro") == OPENROUTER_NANO_BANANA_PRO_MODEL
    assert resolve_multi_ref_group_model_key("gpt_image_2") == OPENROUTER_NANO_BANANA_PRO_MODEL
    assert resolve_multi_ref_group_model_key("flux_2_pro") == OPENROUTER_NANO_BANANA_PRO_MODEL


def test_resolve_multi_ref_group_fallbacks_excludes_flux() -> None:
    flux_fb = resolve_multi_ref_group_fallbacks("flux_2_pro")
    assert OPENROUTER_FLUX_PAID_MODEL not in flux_fb
    assert flux_fb == (OPENROUTER_NANO_BANANA2_MODEL,)


@pytest.mark.asyncio
async def test_run_photo_group_generation_turn_keeps_selected_model(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def _spend(_uid: int, model_id: str):
        captured["model_id"] = model_id
        from services.billing.types import ChargeBreakdown, SpendResult

        return SpendResult(
            ok=True,
            charge=ChargeBreakdown(
                charge_id="c1",
                used_photo_free_slot=False,
                crystals=35,
            ),
        )

    async def _get_user_row(_uid: int):
        return SimpleNamespace(tariff="mini")

    monkeypatch.setattr(
        "services.use_cases.photo_generation_turn.billing.spend_image_resource",
        _spend,
    )
    monkeypatch.setattr(
        "services.repository.get_user_row",
        _get_user_row,
    )

    settings = Settings(tg_token="t")
    result = await run_photo_generation_turn(
        settings,
        None,
        1,
        42,
        "flux_2_pro",
        "Flux 2 Pro",
        "мама и дочка на пляже",
        group_multi_ref=True,
        group_ref_file_ids=["a", "b"],
    )
    assert result.outcome is PhotoGenOutcome.SUCCESS
    assert captured["model_id"] == "flux_2_pro"
    assert result.enqueue is not None
    assert result.enqueue.image_model_id == "flux_2_pro"


@pytest.mark.asyncio
async def test_run_photo_group_generation_turn_need_prompt(monkeypatch) -> None:
    async def _noop_spend(*args, **kwargs):
        raise AssertionError("spend should not be called without prompt")

    monkeypatch.setattr(
        "services.use_cases.photo_generation_turn.billing.spend_image_resource",
        _noop_spend,
    )
    from config import Settings

    settings = Settings(tg_token="t")
    result = await run_photo_generation_turn(
        settings,
        None,
        1,
        42,
        "nano_banana_pro",
        "Nano Banana Pro",
        "",
        group_multi_ref=True,
        group_ref_file_ids=["a", "b"],
    )
    assert result.outcome is PhotoGenOutcome.NEED_PROMPT


@pytest.mark.asyncio
async def test_run_photo_group_generation_turn_rejects_single_ref(monkeypatch) -> None:
    from config import Settings

    settings = Settings(tg_token="t")
    with pytest.raises(ValueError, match="2–10"):
        await run_photo_generation_turn(
            settings,
            None,
            1,
            42,
            "nano_banana_pro",
            "Nano Banana Pro",
            "group shot",
            group_multi_ref=True,
            group_ref_file_ids=["only_one"],
        )
