"""Каталог FREE-моделей OpenRouter."""

from __future__ import annotations

import httpx
import pytest

from services import free_models_catalog as cat


@pytest.fixture(autouse=True)
def _reset_cache():
    cat.reset_free_models_cache_for_tests()
    yield
    cat.reset_free_models_cache_for_tests()


def test_rank_free_models_prefers_giants() -> None:
    online = [
        "qwen/qwen-2.5-7b-instruct:free",
        "deepseek/deepseek-r1-distill-llama-8b:free",
        "some/other:free",
    ]
    ranked = cat.rank_free_models(online)
    assert ranked[0] == "deepseek/deepseek-r1-distill-llama-8b:free"
    assert "meta-llama/llama-3.1-8b-instruct:free" not in ranked
    assert ranked[-1] == "some/other:free"


@pytest.mark.asyncio
async def test_fetch_active_free_models_filters_and_orders() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(cat.OPENROUTER_MODELS_URL)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "black-forest-labs/flux:free",
                        "architecture": {"modality": "text+image->image"},
                    },
                    {
                        "id": "qwen/qwen-2.5-7b-instruct:free",
                        "context_length": 8192,
                        "architecture": {"modality": "text->text"},
                    },
                    {
                        "id": "deepseek/deepseek-r1-distill-llama-8b:free",
                        "context_length": 32000,
                        "architecture": {
                            "modality": "text->text",
                            "input_modalities": ["text"],
                        },
                    },
                    {"id": "google/gemini-2.5-flash", "context_length": 100000},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        models = await cat.fetch_active_free_models(client=client)
    assert models[0] == "deepseek/deepseek-r1-distill-llama-8b:free"
    assert "qwen/qwen-2.5-7b-instruct:free" in models
    assert "black-forest-labs/flux:free" not in models
    assert "google/gemini-2.5-flash" not in models


@pytest.mark.asyncio
async def test_fetch_active_free_models_fallback_on_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        models = await cat.fetch_active_free_models(client=client)
    assert models == cat.emergency_free_models()


@pytest.mark.asyncio
async def test_free_cascade_from_cache_caps() -> None:
    cat._cache_models = [f"m{i}:free" for i in range(20)]
    cascade = cat.free_cascade_from_cache()
    assert len(cascade) == cat.FREE_CASCADE_MAX_MODELS
    assert all(m.endswith(":free") for m in cascade)


def test_model_route_uses_live_cache() -> None:
    from services.billing.chat_pipeline import _model_route_for_role
    from services.billing.types import TariffTier

    cat._cache_models = [
        "meta-llama/llama-3.1-8b-instruct:free",
        "qwen/qwen-2.5-7b-instruct:free",
    ]
    primary, fb = _model_route_for_role("standard", TariffTier.FREE)
    assert primary == "meta-llama/llama-3.1-8b-instruct:free"
    assert "qwen/qwen-2.5-7b-instruct:free" in fb


def test_model_route_falls_back_to_hardcoded_when_cache_empty() -> None:
    from services.billing.chat_pipeline import _model_route_for_role
    from services.billing.types import TariffTier

    primary, fb = _model_route_for_role("standard", TariffTier.FREE)
    # empty cache → emergency (2) via free_cascade_from_cache
    assert primary.endswith(":free")
    assert all(m.endswith(":free") for m in (primary, *fb))
    assert "google/gemini-2.5-flash" not in fb
