"""Mini App API: GET /api/v1/reports/{report_id}."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services import repository as repo

SAMPLE_JSON = (
    '{"title":"Доход","headers":["Месяц","Доход"],'
    '"rows":[["Янв",1200],["Фев",1500]]}'
)


@pytest.fixture
def mini_app_client(tmp_path, monkeypatch):
    db_path = tmp_path / "miniapp_test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setattr(repo, "DB_PATH", str(db_path))

    from importlib import reload

    import api.mini_app as mini_app_module

    reload(mini_app_module)
    with TestClient(mini_app_module.app) as client:
        yield client


@pytest.mark.asyncio
async def test_insert_and_fetch_table_report(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "reports.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setattr(repo, "DB_PATH", str(db_path))
    await repo.init_db()
    await repo.ensure_user(1001)
    report_id = await repo.insert_table_report(1001, SAMPLE_JSON)
    data = await repo.fetch_table_report_json(report_id)
    assert data is not None
    assert data["title"] == "Доход"
    assert data["headers"] == ["Месяц", "Доход"]
    assert data["rows"][0] == ["Янв", "1200"]


def test_get_report_data_endpoint(mini_app_client) -> None:
  import asyncio

  async def _seed() -> int:
      await repo.init_db()
      await repo.ensure_user(42)
      return await repo.insert_table_report(42, SAMPLE_JSON)

  report_id = asyncio.run(_seed())
  resp = mini_app_client.get(f"/api/v1/reports/{report_id}")
  assert resp.status_code == 200
  body = resp.json()
  assert body["report_id"] == report_id
  assert body["table_raw_json"]["title"] == "Доход"
  assert body["table_raw_json"]["headers"] == ["Месяц", "Доход"]


def test_get_report_not_found(mini_app_client) -> None:
    resp = mini_app_client.get("/api/v1/reports/999999")
    assert resp.status_code == 404
