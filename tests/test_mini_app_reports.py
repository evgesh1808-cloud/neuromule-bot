"""Mini App API: GET /api/v1/reports/{report_id} + initData auth."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from api.auth import sign_init_data_for_tests
from services import repository as repo

SAMPLE_JSON = (
    '{"title":"Доход","headers":["Месяц","Доход"],'
    '"rows":[["Янв",1200],["Фев",1500]]}'
)

_TEST_BOT_TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


@pytest.fixture
def mini_app_client(tmp_path, monkeypatch):
    db_path = tmp_path / "miniapp_test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setattr(repo, "DB_PATH", str(db_path))
    monkeypatch.setattr("api.auth._bot_token", lambda: _TEST_BOT_TOKEN)

    from importlib import reload

    import api.mini_app as mini_app_module

    reload(mini_app_module)
    with TestClient(mini_app_module.app) as client:
        yield client


def _auth_headers(user_id: int) -> dict[str, str]:
    init_data = sign_init_data_for_tests(_TEST_BOT_TOKEN, user_id=user_id)
    return {"Authorization": f"tma {init_data}"}


@pytest.mark.asyncio
async def test_insert_and_fetch_table_report_for_user(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "reports.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setattr(repo, "DB_PATH", str(db_path))
    await repo.init_db()
    await repo.ensure_user(1001)
    report_id = await repo.insert_table_report(1001, SAMPLE_JSON)
    data = await repo.fetch_table_report_json_for_user(report_id, 1001)
    assert data is not None
    assert data["title"] == "Доход"
    denied = await repo.fetch_table_report_json_for_user(report_id, 9999)
    assert denied is None


def test_get_report_data_endpoint_owner(mini_app_client) -> None:
    async def _seed() -> int:
        await repo.init_db()
        await repo.ensure_user(42)
        return await repo.insert_table_report(42, SAMPLE_JSON)

    report_id = asyncio.run(_seed())
    resp = mini_app_client.get(
        f"/api/v1/reports/{report_id}",
        headers=_auth_headers(42),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["report_id"] == report_id
    assert body["table_raw_json"]["title"] == "Доход"


def test_get_report_data_forbidden_without_auth(mini_app_client) -> None:
    async def _seed() -> int:
        await repo.init_db()
        await repo.ensure_user(42)
        return await repo.insert_table_report(42, SAMPLE_JSON)

    report_id = asyncio.run(_seed())
    resp = mini_app_client.get(f"/api/v1/reports/{report_id}")
    assert resp.status_code == 401


def test_get_report_data_idor_other_user(mini_app_client) -> None:
    async def _seed() -> int:
        await repo.init_db()
        await repo.ensure_user(42)
        await repo.ensure_user(77)
        return await repo.insert_table_report(42, SAMPLE_JSON)

    report_id = asyncio.run(_seed())
    resp = mini_app_client.get(
        f"/api/v1/reports/{report_id}",
        headers=_auth_headers(77),
    )
    assert resp.status_code == 404


def test_get_report_not_found(mini_app_client) -> None:
    resp = mini_app_client.get(
        "/api/v1/reports/999999",
        headers=_auth_headers(1),
    )
    assert resp.status_code == 404
