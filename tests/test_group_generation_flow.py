"""Tests for multi-reference group photo mode."""

from __future__ import annotations

import pytest

from content import messages as msg
from content.inline_keyboards import group_photo_collector_keyboard
from services.openrouter_images import build_multi_banana_prompt, build_multi_ref_group_payload
from services.use_cases.photo_generation_turn import PhotoGenOutcome, run_photo_generation_turn


def test_build_multi_banana_prompt_identity_lines() -> None:
    prompt = build_multi_banana_prompt("family on the beach at sunset", 3)
    assert "CRITICAL MULTI-SUBJECT IDENTITY DIRECTIVE" in prompt
    assert "exactly 3 individual face images" in prompt
    assert "input_references[0]" in prompt
    assert "input_references[1]" in prompt
    assert "input_references[2]" in prompt
    assert "STRICTLY FORBIDDEN to blend" in prompt
    assert "family on the beach at sunset" in prompt
    assert "[Negative prompt:" in prompt


def test_build_multi_ref_group_payload_requires_two_refs() -> None:
    with pytest.raises(Exception):
        build_multi_ref_group_payload("group portrait", ["https://example.com/a.png"])
    payload = build_multi_ref_group_payload(
        "group portrait",
        ["https://example.com/a.png", "https://example.com/b.png"],
    )
    assert len(payload["input_references"]) == 2
    assert "Persona 1" in str(payload["prompt"])
    assert "Persona 2" in str(payload["prompt"])


def test_group_photo_collector_keyboard_gating() -> None:
    locked = group_photo_collector_keyboard(can_generate=False)
    ready = group_photo_collector_keyboard(can_generate=True)
    assert locked.inline_keyboard[0][0].callback_data == "grp_photo_gen_locked"
    assert ready.inline_keyboard[0][0].callback_data == msg.CB_GROUP_PHOTO_GENERATE


def test_format_group_photo_status_html() -> None:
    text = msg.format_group_photo_status_html(refs_count=4, group_prompt="")
    assert "4" in text
    assert "Не указан" in text
    text2 = msg.format_group_photo_status_html(refs_count=2, group_prompt="все улыбаются")
    assert "все улыбаются" in text2


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
