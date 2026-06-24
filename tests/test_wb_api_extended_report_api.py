"""Mini App API: расширенный JSON отчёта WB (abc_analysis, out_of_stock_forecast)."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from api.auth import sign_init_data_for_tests
from services import repository as repo
from services.wb_api.analytics import (
    build_compact_digest,
    compute_product_margins,
    run_abc_analysis,
    run_out_of_stock_forecasts,
)
from services.wb_api.report_builder import build_extended_report_json

_TEST_BOT_TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


@pytest.fixture
def mini_app_client(tmp_path, monkeypatch):
    db_path = tmp_path / "wb_extended.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setattr(repo, "DB_PATH", str(db_path))
    monkeypatch.setattr("api.auth._bot_token", lambda: _TEST_BOT_TOKEN)

    from importlib import reload

    import api.mini_app as mini_app_module

    reload(mini_app_module)
    with TestClient(mini_app_module.app) as client:
        yield client


def test_get_report_includes_abc_and_oos(mini_app_client) -> None:
    rows = [
        {
            "sku": "1",
            "name": "WRAPPER",
            "revenue": 80_000,
            "commission": 8_000,
            "logistics": 4_000,
            "ad_cost": 1_000,
            "stock_qty": 25,
            "sales_7d_qty": 50,
        },
        {
            "sku": "2",
            "name": "BOX",
            "revenue": 15_000,
            "commission": 2_000,
            "logistics": 1_000,
            "ad_cost": 200,
            "stock_qty": 5,
            "sales_7d_qty": 14,
        },
    ]
    products = compute_product_margins(rows)
    abc = run_abc_analysis(products)
    oos = run_out_of_stock_forecasts(products)
    digest = build_compact_digest(products, abc, oos)
    table_json = build_extended_report_json(
        products=products,
        abc=abc,
        oos_forecasts=oos,
        digest=digest,
    )

    async def _seed() -> int:
        await repo.init_db()
        await repo.ensure_user(55)
        return await repo.insert_table_report(55, table_json)

    report_id = asyncio.run(_seed())
    init_data = sign_init_data_for_tests(_TEST_BOT_TOKEN, user_id=55)
    resp = mini_app_client.get(
        f"/api/v1/reports/{report_id}",
        headers={"Authorization": f"tma {init_data}"},
    )
    assert resp.status_code == 200
    raw = resp.json()["table_raw_json"]
    assert "abc_analysis" in raw
    assert "out_of_stock_forecast" in raw
    assert raw["abc_analysis"]["group_a"]
    assert any(item["risk_out_of_stock"] for item in raw["out_of_stock_forecast"])
    assert raw["summary"]["group_a_leader"] == "WRAPPER"
