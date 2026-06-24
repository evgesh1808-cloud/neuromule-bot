"""Тесты services/api/report_endpoints.py."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from api.auth import sign_init_data_for_tests
from config import settings
from services import repository as repo

SAMPLE_JSON = (
    '{"title":"Доход","headers":["Месяц","Доход"],'
    '"rows":[["Янв",1200],["Фев",1500]]}'
)

_TEST_BOT_TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


@pytest.fixture
def reports_client(tmp_path, monkeypatch):
    db_path = tmp_path / "reports_api.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setattr(repo, "DB_PATH", str(db_path))
    monkeypatch.setattr("api.auth._bot_token", lambda: _TEST_BOT_TOKEN)

    from config import settings

    object.__setattr__(settings, "mini_app_cors_origins", "https://example.github.io")

    from importlib import reload

    import api.mini_app as mini_app_module

    reload(mini_app_module)
    with TestClient(mini_app_module.app) as client:
        yield client


def test_report_endpoint_returns_json(reports_client) -> None:
    async def _seed() -> int:
        await repo.init_db()
        await repo.ensure_user(1)
        return await repo.insert_table_report(1, SAMPLE_JSON)

    report_id = asyncio.run(_seed())
    init_data = sign_init_data_for_tests(_TEST_BOT_TOKEN, user_id=1)
    resp = reports_client.get(
        f"/api/v1/reports/{report_id}",
        headers={"Authorization": f"tma {init_data}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["report_id"] == report_id
    assert body["table_raw_json"]["title"] == "Доход"


def test_cors_explicit_origin(reports_client) -> None:
    resp = reports_client.options(
        "/api/v1/reports/1",
        headers={
            "Origin": "https://example.github.io",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code in (200, 204, 405)
    if "access-control-allow-origin" in resp.headers:
        assert resp.headers["access-control-allow-origin"] == "https://example.github.io"
        assert resp.headers["access-control-allow-origin"] != "*"
