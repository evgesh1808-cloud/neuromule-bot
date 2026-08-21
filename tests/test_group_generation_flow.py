"""Tests for multi-reference group photo payload and billing turn."""

from __future__ import annotations

import pytest

from services.openrouter_images import build_multi_ref_group_payload
from services.use_cases.photo_generation_turn import PhotoGenOutcome, run_photo_generation_turn


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
