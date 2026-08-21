"""Tests for multi-reference group photo payload and billing turn."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest

from config import Settings
from services.openrouter_images import (
    OPENROUTER_FLUX_PAID_MODEL,
    OPENROUTER_GPT_IMAGE2_MODEL,
    OPENROUTER_NANO_BANANA_PRO_MODEL,
    build_multi_ref_group_payload,
    generate_openrouter_multi_ref_group_photo,
    resolve_multi_ref_group_fallbacks,
    resolve_multi_ref_group_model_key,
)
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
async def test_generate_multi_ref_group_uses_structured_prompt() -> None:
    settings = Settings(tg_token="t", openrouter_key="k")
    refs = ["data:image/png;base64,aaa", "data:image/png;base64,bbb"]

    with (
        patch(
            "services.openrouter_images.ensure_png_reference_data_url",
            AsyncMock(side_effect=lambda url: url),
        ),
        patch(
            "services.openrouter_images.build_multi_banana_prompt_from_ru",
            AsyncMock(return_value="CRITICAL MULTI-SUBJECT structured prompt"),
        ) as build_prompt,
        patch(
            "services.openrouter_images.generate_openrouter_image",
            AsyncMock(return_value=type("R", (), {"url": "https://cdn/out.png", "data": None})()),
        ) as gen_image,
    ):
        await generate_openrouter_multi_ref_group_photo(
            settings,
            model="google/gemini-3-pro-image-preview",
            user_prompt="мама и дочка на пляже",
            reference_image_data_urls=refs,
        )

    build_prompt.assert_awaited_once_with(settings, "мама и дочка на пляже", 2)
    assert "CRITICAL MULTI-SUBJECT" in gen_image.await_args.kwargs["prompt"]


def test_resolve_multi_ref_group_model_key_uses_selected_menu_model() -> None:
    assert resolve_multi_ref_group_model_key("nano_banana_pro") == OPENROUTER_NANO_BANANA_PRO_MODEL
    assert resolve_multi_ref_group_model_key("gpt_image_2") == OPENROUTER_GPT_IMAGE2_MODEL
    assert resolve_multi_ref_group_model_key("flux_2_pro") == OPENROUTER_FLUX_PAID_MODEL


def test_resolve_multi_ref_group_fallbacks_respects_primary_model() -> None:
    flux_primary = resolve_multi_ref_group_model_key("flux_2_pro")
    flux_fb = resolve_multi_ref_group_fallbacks("flux_2_pro")
    assert flux_primary not in flux_fb
    assert OPENROUTER_GPT_IMAGE2_MODEL in flux_fb or OPENROUTER_NANO_BANANA_PRO_MODEL in flux_fb


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
