"""Тесты webapp generate API и VK launch params."""

from __future__ import annotations

import hashlib
from importlib import reload
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from api.auth import sign_init_data_for_tests
from api.vk_auth import validate_vk_launch_params
from services.use_cases.photo_generation_turn import PhotoGenOutcome

_TEST_BOT_TOKEN = "123456789:AAH-dummy-token-for-tests"
_VK_SECRET = "test_vk_secret"


def _sign_vk_params(params: dict[str, str], secret: str) -> str:
    base = "&".join(f"{key}={params[key]}" for key in sorted(params))
    sign = hashlib.md5((base + secret).encode("utf-8")).hexdigest()
    fields = dict(params)
    fields["sign"] = sign
    return urlencode(fields)


@pytest.fixture
def mini_app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "webapp_api.db"))
    monkeypatch.setenv("TG_TOKEN", _TEST_BOT_TOKEN)
    monkeypatch.setenv("VK_MINI_APP_SECRET", _VK_SECRET)

    from config import settings

    object.__setattr__(settings, "tg_token", _TEST_BOT_TOKEN)
    object.__setattr__(settings, "vk_mini_app_secret", _VK_SECRET)

    import api.mini_app as mini_app_module

    reload(mini_app_module)
    with TestClient(mini_app_module.app) as client:
        yield client


def test_validate_vk_launch_params_ok(monkeypatch) -> None:
    from config import settings

    object.__setattr__(settings, "vk_mini_app_secret", _VK_SECRET)
    params = {"vk_user_id": "42", "vk_app_id": "123"}
    query = _sign_vk_params(params, _VK_SECRET)
    user = validate_vk_launch_params(query)
    assert user.vk_user_id == 42


def test_webapp_static_index(mini_app_client) -> None:
    resp = mini_app_client.get("/webapp/")
    assert resp.status_code == 200
    assert "NeuroMule Studio" in resp.text
    assert "telegram-web-app.js" in resp.text
    assert "Создать арт" in resp.text


def test_webapp_generate_telegram(mini_app_client, monkeypatch) -> None:
    pipeline_calls: list[dict] = []

    async def _fake_pipeline(**kwargs):
        pipeline_calls.append(kwargs)
        return PhotoGenOutcome.SUCCESS, None

    monkeypatch.setattr(
        "ports.webapp_endpoints.run_webapp_image_pipeline",
        _fake_pipeline,
    )

    init_data = sign_init_data_for_tests(_TEST_BOT_TOKEN, user_id=9001)
    resp = mini_app_client.post(
        "/api/webapp/generate",
        headers={"Authorization": f"tma {init_data}"},
        json={
            "user_id": 9001,
            "platform": "telegram",
            "model_key": "flux-schnell",
            "aspect_ratio": "16:9",
            "prompt": "sunset over mountains",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert pipeline_calls
    assert pipeline_calls[0]["aspect_ratio"] == "16:9"
    assert pipeline_calls[0]["model_id"] == "flux_schnell"


def test_webapp_generate_rejects_user_mismatch(mini_app_client) -> None:
    init_data = sign_init_data_for_tests(_TEST_BOT_TOKEN, user_id=1)
    resp = mini_app_client.post(
        "/api/webapp/generate",
        headers={"Authorization": f"tma {init_data}"},
        json={
            "user_id": 999,
            "platform": "telegram",
            "model_key": "flux-schnell",
            "aspect_ratio": "1:1",
            "prompt": "test",
        },
    )
    assert resp.status_code == 403
