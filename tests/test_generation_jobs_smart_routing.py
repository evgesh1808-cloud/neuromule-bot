"""Умный роутинг моделей фото (Chatcom-style) в generation_jobs."""

from __future__ import annotations

from services.generation_jobs import (
    resolve_smart_photo_model_key,
    _is_text_design_intent,
)


def test_selfie_routes_to_nano_banana_pro() -> None:
    assert resolve_smart_photo_model_key(
        "flux_2_pro",
        has_reference=True,
        prompt="portrait on beach",
    ) == "nano_banana_pro"
    assert resolve_smart_photo_model_key(
        "gpt_image_2",
        has_reference=True,
        prompt="studio",
    ) == "gpt_image_2"
    assert resolve_smart_photo_model_key(
        "nano_banana_2",
        has_reference=True,
        prompt="portrait",
    ) == "nano_banana_2"
    assert resolve_smart_photo_model_key(
        "flux_schnell",
        has_reference=True,
        prompt="portrait on beach",
    ) == "nano_banana_pro"


def test_selfie_already_pro_stays() -> None:
    assert resolve_smart_photo_model_key(
        "nano_banana_pro",
        has_reference=True,
        prompt="portrait",
    ) == "nano_banana_pro"


def test_text_only_nano_routes_to_flux() -> None:
    assert resolve_smart_photo_model_key(
        "nano_banana_pro",
        has_reference=False,
        prompt="Epic mountain landscape",
    ) == "flux_2_pro"


def test_text_design_intent_routes_to_flux() -> None:
    assert _is_text_design_intent("Modern architecture blueprint of a skyscraper")
    assert resolve_smart_photo_model_key(
        "nano_banana_2",
        has_reference=False,
        prompt="Logo design with typography",
    ) == "flux_2_pro"


def test_flux_text_only_unchanged() -> None:
    assert resolve_smart_photo_model_key(
        "flux_2_pro",
        has_reference=False,
        prompt="sunset over mountains",
    ) == "flux_2_pro"
