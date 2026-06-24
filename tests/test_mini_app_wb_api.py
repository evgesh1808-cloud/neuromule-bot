"""Mini App API: WB autopilot setup/toggle."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from api.auth import sign_init_data_for_tests
from services import repository as repo

_TEST_BOT_TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


@pytest.fixture
def wb_client(tmp_path, monkeypatch):
    db_path = tmp_path / "wb_api_test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setattr(repo, "DB_PATH", str(db_path))
    monkeypatch.setattr("api.auth._bot_token", lambda: _TEST_BOT_TOKEN)

    from importlib import reload

    import api.mini_app as mini_app_module

    reload(mini_app_module)
    with TestClient(mini_app_module.app) as client:
        yield client


def _headers(user_id: int) -> dict[str, str]:
    init_data = sign_init_data_for_tests(_TEST_BOT_TOKEN, user_id=user_id)
    return {"Authorization": f"tma {init_data}"}


def test_wb_setup_and_toggle(wb_client) -> None:
    async def _seed() -> None:
        await repo.init_db()
        await repo.ensure_user(501)

    asyncio.run(_seed())
    h = _headers(501)

    status = wb_client.get("/api/v1/wb/status", headers=h)
    assert status.status_code == 200
    assert status.json()["has_token"] is False

    setup = wb_client.post(
        "/api/v1/wb/setup",
        headers=h,
        json={"api_token": "test-wb-token-secret", "enabled": True},
    )
    assert setup.status_code == 200
    assert setup.json()["ok"] is True
    assert setup.json()["enabled"] is True

    toggle_off = wb_client.post(
        "/api/v1/wb/toggle",
        headers=h,
        json={"enabled": False},
    )
    assert toggle_off.status_code == 200
    assert toggle_off.json()["enabled"] is False

    toggle_on = wb_client.post(
        "/api/v1/wb/toggle",
        headers=h,
        json={"enabled": True},
    )
    assert toggle_on.status_code == 200
    assert toggle_on.json()["daily_crystals"] == 50


def test_wb_toggle_without_token_fails(wb_client) -> None:
    async def _seed() -> None:
        await repo.init_db()
        await repo.ensure_user(777)

    asyncio.run(_seed())
    resp = wb_client.post(
        "/api/v1/wb/toggle",
        headers=_headers(777),
        json={"enabled": True},
    )
    assert resp.status_code == 400
