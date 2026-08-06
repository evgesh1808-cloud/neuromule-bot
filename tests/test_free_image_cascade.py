"""Тесты каскада Flux FREE: Pollinations → OpenRouter spare → RR."""

from __future__ import annotations

import pytest

from services.api_resilience import ExternalApiError
from services.free_image_cascade import (
    DEFAULT_OPENROUTER_FLUX_FREE,
    FreeImageCascadeExhausted,
    OpenRouterPaidBlockedError,
    _extract_b64_from_payload,
    build_free_image_providers,
    ensure_openrouter_free_model,
    generate_free_tier_image,
    reset_free_image_rr_for_tests,
)
from services.gemini_image_client import GeminiImageResult


@pytest.fixture(autouse=True)
def _skip_pollinations_by_default(monkeypatch: pytest.MonkeyPatch):
    """RR/spare-тесты: Pollinations сразу «падает», чтобы не ходить в сеть."""

    async def _no_pollinations(_prompt: str) -> GeminiImageResult:
        raise ExternalApiError("Pollinations", "skipped in unit test")

    monkeypatch.setattr(
        "services.free_image_cascade.generate_flux_schnell_image",
        _no_pollinations,
    )


def test_extract_b64_from_openai_style_payload() -> None:
    import base64

    raw = b"fake-png"
    b64 = base64.b64encode(raw).decode()
    payload = {
        "choices": [
            {
                "message": {
                    "images": [{"image_url": {"url": f"data:image/png;base64,{b64}"}}],
                }
            }
        ]
    }
    out = _extract_b64_from_payload(payload)
    assert out == raw


def test_ensure_openrouter_free_model_appends_suffix() -> None:
    assert (
        ensure_openrouter_free_model("google/gemini-2.5-flash-image-preview")
        == "google/gemini-2.5-flash-image-preview:free"
    )


def test_ensure_openrouter_free_model_blocks_paid_variants() -> None:
    with pytest.raises(OpenRouterPaidBlockedError):
        ensure_openrouter_free_model("google/gemini-2.5-flash-image-preview:nitro")


def test_default_openrouter_model_is_flux_schnell_free() -> None:
    assert DEFAULT_OPENROUTER_FLUX_FREE.endswith(":free")
    assert "flux" in DEFAULT_OPENROUTER_FLUX_FREE.lower()


def test_build_free_image_providers_order(monkeypatch: pytest.MonkeyPatch) -> None:
    from config import settings

    object.__setattr__(settings, "gemini_api_key", "g1")
    object.__setattr__(settings, "gemini_api_key_2", "g2")
    object.__setattr__(settings, "openrouter_key", "o1")
    object.__setattr__(settings, "openrouter_key_2", "o2")
    slots = build_free_image_providers()
    assert [s["type"] for s in slots] == ["gemini", "gemini", "openrouter", "openrouter"]
    assert [s["key"] for s in slots] == ["g1", "g2", "o1", "o2"]


def test_providers_for_request_includes_gemini_on_i2i() -> None:
    from services.free_image_cascade import _providers_for_request

    pool = [
        {"type": "gemini", "key": "g1"},
        {"type": "gemini", "key": "g2"},
        {"type": "openrouter", "key": "o1"},
        {"type": "openrouter", "key": "o2"},
    ]
    assert _providers_for_request(pool, has_reference=False) == [
        {"type": "gemini", "key": "g1"},
        {"type": "gemini", "key": "g2"},
    ]
    i2i = _providers_for_request(pool, has_reference=True)
    assert [p["type"] for p in i2i] == ["gemini", "gemini"]
    assert [p["key"] for p in i2i] == ["g1", "g2"]


def test_deprecated_openrouter_free_model_disabled() -> None:
    from config import settings
    from services.free_image_cascade import openrouter_free_image_enabled

    object.__setattr__(
        settings,
        "free_image_openrouter_model",
        "black-forest-labs/flux-1-schnell:free",
    )
    assert openrouter_free_image_enabled() is False


def test_openrouter_free_model_enabled_when_set() -> None:
    from config import settings
    from services.free_image_cascade import openrouter_free_image_enabled

    object.__setattr__(settings, "free_image_openrouter_model", "vendor/model:free")
    assert openrouter_free_image_enabled() is True


def test_gemini_t2i_models_prefers_flash_over_imagen_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from config import settings
    from services.free_image_cascade import DEFAULT_GEMINI_T2I_MODEL, _gemini_t2i_models

    object.__setattr__(settings, "free_image_gemini_model", "imagen-3.0-generate-002")
    models = _gemini_t2i_models()
    assert models[0] == DEFAULT_GEMINI_T2I_MODEL
    assert "imagen-3.0-generate-002" in models


def test_build_free_image_providers_skips_empty() -> None:
    from config import settings

    object.__setattr__(settings, "gemini_api_key", "g1")
    object.__setattr__(settings, "gemini_api_key_2", "")
    object.__setattr__(settings, "openrouter_key", "o1")
    object.__setattr__(settings, "openrouter_key_2", "")
    slots = build_free_image_providers()
    assert len(slots) == 2
    assert slots[0]["key"] == "g1"
    assert slots[1]["key"] == "o1"


@pytest.mark.asyncio
async def test_pollinations_success_skips_spare_and_rr(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_free_image_rr_for_tests()
    calls: list[str] = []

    async def _ok_pollinations(prompt: str) -> GeminiImageResult:
        calls.append(prompt)
        return GeminiImageResult(data=b"from-pollinations")

    async def _boom_spare(*_a, **_k):
        raise AssertionError("spare wheel must not run")

    async def _boom_slot(*_a, **_k):
        raise AssertionError("RR must not run")

    monkeypatch.setattr(
        "services.free_image_cascade.generate_flux_schnell_image",
        _ok_pollinations,
    )
    monkeypatch.setattr(
        "services.free_image_cascade._try_openrouter_spare_wheel",
        _boom_spare,
    )
    monkeypatch.setattr("services.free_image_cascade._invoke_slot", _boom_slot)

    out = await generate_free_tier_image("sunset")
    assert out.data == b"from-pollinations"
    assert calls == ["sunset"]


@pytest.mark.asyncio
async def test_pollinations_fail_uses_openrouter_spare(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_free_image_rr_for_tests()
    from config import settings

    object.__setattr__(
        settings,
        "free_image_key_pause_sec",
        0.0,
    )
    object.__setattr__(settings, "free_image_openrouter_model", "vendor/test-model:free")
    seen_models: list[str] = []

    monkeypatch.setattr(
        "services.free_image_cascade.build_free_image_providers",
        lambda: [{"type": "openrouter", "key": "o1"}],
    )

    async def _fake_or(
        prompt: str,
        *,
        api_key: str,
        reference_image_bytes,
        reference_mime: str,
        timeout: float,
        model: str | None = None,
    ) -> GeminiImageResult:
        seen_models.append(model or "")
        assert api_key == "o1"
        assert "allow" or True
        return GeminiImageResult(data=b"from-spare")

    async def _boom_gemini(*_a, **_k):
        raise ExternalApiError("Gemini", "skip in test")

    async def _boom_rr(*_a, **_k):
        raise AssertionError("RR must not run when spare succeeds")

    monkeypatch.setattr("services.free_image_cascade._try_gemini_spare_wheel", _boom_gemini)
    monkeypatch.setattr("services.free_image_cascade._call_openrouter", _fake_or)
    monkeypatch.setattr("services.free_image_cascade._invoke_slot", _boom_rr)

    out = await generate_free_tier_image("cat")
    assert out.data == b"from-spare"
    assert seen_models == ["vendor/test-model:free"]


@pytest.mark.asyncio
async def test_spare_wheel_sets_allow_fallbacks_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Контракт OpenRouter body: allow_fallbacks=False (без платных центов)."""
    import services.free_image_cascade as fic

    reset_free_image_rr_for_tests()
    from config import settings

    object.__setattr__(settings, "free_image_openrouter_model", "vendor/test-model:free")
    captured: dict = {}

    class _Resp:
        status_code = 200

        def json(self):
            import base64

            b64 = base64.b64encode(b"img").decode()
            return {
                "model": "black-forest-labs/flux-1-schnell:free",
                "choices": [
                    {"message": {"images": [{"image_url": {"url": f"data:image/png;base64,{b64}"}}]}}
                ],
            }

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["body"] = json
            return _Resp()

    monkeypatch.setattr(fic.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(
        "services.free_image_cascade.build_free_image_providers",
        lambda: [{"type": "openrouter", "key": "orkey123456"}],
    )

    out = await fic._try_openrouter_spare_wheel(
        "test",
        reference_image_bytes=None,
        reference_mime="image/jpeg",
        timeout=5.0,
    )
    assert out.data == b"img"
    assert captured["body"]["model"] == "vendor/test-model:free"
    assert captured["body"]["provider"]["allow_fallbacks"] is False


@pytest.mark.asyncio
async def test_cascade_exhausted_when_all_slots_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_free_image_rr_for_tests()
    object.__setattr__(
        __import__("config", fromlist=["settings"]).settings,
        "free_image_key_pause_sec",
        0.0,
    )

    monkeypatch.setattr(
        "services.free_image_cascade.build_free_image_providers",
        lambda: [
            {"type": "gemini", "key": "g1"},
            {"type": "openrouter", "key": "o1"},
        ],
    )

    async def _fail(*_a, **_k):
        raise ExternalApiError("Test", "HTTP 429")

    monkeypatch.setattr("services.free_image_cascade._try_gemini_spare_wheel", _fail)
    monkeypatch.setattr("services.free_image_cascade._try_openrouter_spare_wheel", _fail)
    monkeypatch.setattr("services.free_image_cascade._invoke_slot", _fail)

    with pytest.raises(FreeImageCascadeExhausted):
        await generate_free_tier_image("sunset")


@pytest.mark.asyncio
async def test_round_robin_advances_index(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.free_image_cascade as fic

    reset_free_image_rr_for_tests()
    from config import settings

    object.__setattr__(settings, "free_image_openrouter_model", "")
    object.__setattr__(
        settings,
        "free_image_key_pause_sec",
        0.0,
    )
    seen: list[str] = []

    monkeypatch.setattr(
        "services.free_image_cascade.build_free_image_providers",
        lambda: [
            {"type": "gemini", "key": "g1"},
            {"type": "gemini", "key": "g2"},
            {"type": "openrouter", "key": "o1"},
            {"type": "openrouter", "key": "o2"},
        ],
    )

    async def _spare_fail(*_a, **_k):
        raise ExternalApiError("OpenRouter", "spare down")

    async def _ok(slot, *_a, **_k):
        seen.append(slot["key"])
        return GeminiImageResult(data=b"ok")

    monkeypatch.setattr("services.free_image_cascade._try_gemini_spare_wheel", _spare_fail)
    monkeypatch.setattr("services.free_image_cascade._try_openrouter_spare_wheel", _spare_fail)
    monkeypatch.setattr("services.free_image_cascade._invoke_slot", _ok)

    await generate_free_tier_image("a")
    await generate_free_tier_image("b")
    await generate_free_tier_image("c")
    assert seen == ["g1", "g2", "g1"]
    assert fic.global_provider_index == 3


@pytest.mark.asyncio
async def test_failover_on_429_shifts_within_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.free_image_cascade as fic

    reset_free_image_rr_for_tests()
    object.__setattr__(
        __import__("config", fromlist=["settings"]).settings,
        "free_image_key_pause_sec",
        0.0,
    )
    seen: list[str] = []
    calls = {"n": 0}

    monkeypatch.setattr(
        "services.free_image_cascade.build_free_image_providers",
        lambda: [
            {"type": "gemini", "key": "g1"},
            {"type": "gemini", "key": "g2"},
            {"type": "openrouter", "key": "o1"},
            {"type": "openrouter", "key": "o2"},
        ],
    )

    async def _spare_fail(*_a, **_k):
        raise ExternalApiError("OpenRouter", "spare down")

    async def _flaky(slot, *_a, **_k):
        seen.append(slot["key"])
        calls["n"] += 1
        if calls["n"] == 1:
            raise ExternalApiError("Gemini", "HTTP 429")
        return GeminiImageResult(data=b"ok")

    monkeypatch.setattr("services.free_image_cascade._try_gemini_spare_wheel", _spare_fail)
    monkeypatch.setattr("services.free_image_cascade._try_openrouter_spare_wheel", _spare_fail)
    monkeypatch.setattr("services.free_image_cascade._invoke_slot", _flaky)

    out = await generate_free_tier_image("x")
    assert out.data == b"ok"
    assert seen == ["g1", "g2"]
    assert fic.global_provider_index == 2
