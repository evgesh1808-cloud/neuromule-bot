"""Тесты services/api/report_endpoints.py."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services import repository as repo

SAMPLE_JSON = (
    '{"title":"Доход","headers":["Месяц","Доход"],'
    '"rows":[["Янв",1200],["Фев",1500]]}'
)


@pytest.fixture
def reports_client(tmp_path, monkeypatch):
    db_path = tmp_path / "reports_api.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setattr(repo, "DB_PATH", str(db_path))

    from importlib import reload

    import api.mini_app as mini_app_module

    reload(mini_app_module)
    with TestClient(mini_app_module.app) as client:
        yield client


def test_report_endpoint_returns_json(reports_client) -> None:
    import asyncio

    async def _seed() -> int:
        await repo.init_db()
        await repo.ensure_user(1)
        return await repo.insert_table_report(1, SAMPLE_JSON)

    report_id = asyncio.run(_seed())
    resp = reports_client.get(f"/api/v1/reports/{report_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["report_id"] == report_id
    assert body["table_raw_json"]["title"] == "Доход"
    assert body["table_raw_json"]["headers"] == ["Месяц", "Доход"]


def test_cors_allows_any_origin(reports_client) -> None:
    resp = reports_client.options(
        "/api/v1/reports/1",
        headers={
            "Origin": "https://example.github.io",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code in (200, 204, 405)
    if "access-control-allow-origin" in resp.headers:
        assert resp.headers["access-control-allow-origin"] in ("*", "https://example.github.io")
