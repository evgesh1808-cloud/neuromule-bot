"""Тесты локальной оцифровки WB (ABC, OOS, digest)."""

from __future__ import annotations

from services.wb_api.analytics import (
    build_compact_digest,
    compute_product_margins,
    forecast_out_of_stock,
    run_abc_analysis,
    run_out_of_stock_forecasts,
)
from services.wb_api.types import WbProductMetrics


def _sample_rows() -> list[dict]:
    return [
        {
            "sku": "A1",
            "name": "WRAPPER",
            "revenue": 100_000,
            "commission": 10_000,
            "logistics": 5_000,
            "ad_cost": 2_000,
            "stock_qty": 200,
            "sales_7d_qty": 70,
        },
        {
            "sku": "B2",
            "name": "BOX",
            "revenue": 20_000,
            "commission": 3_000,
            "logistics": 1_000,
            "ad_cost": 500,
            "stock_qty": 6,
            "sales_7d_qty": 14,
        },
        {
            "sku": "C3",
            "name": "DEAD",
            "revenue": 5_000,
            "commission": 4_000,
            "logistics": 2_000,
            "ad_cost": 0,
            "stock_qty": 100,
            "sales_7d_qty": 0,
        },
    ]


def test_compute_product_margins() -> None:
    products = compute_product_margins(_sample_rows())
    assert len(products) == 3
    wrapper = next(p for p in products if p.name == "WRAPPER")
    assert wrapper.margin == 83_000


def test_abc_analysis_top_a_and_dead_c() -> None:
    products = compute_product_margins(_sample_rows())
    abc = run_abc_analysis(products)
    assert any(i.name == "WRAPPER" for i in abc.group_a)
    assert any(i.name == "DEAD" for i in abc.group_c)


def test_out_of_stock_risk_and_fomo() -> None:
    products = compute_product_margins(_sample_rows())
    box = next(p for p in products if p.name == "BOX")
    forecast = forecast_out_of_stock(box)
    assert forecast.risk_out_of_stock is True
    assert forecast.days_until_stockout is not None
    assert forecast.days_until_stockout < 7
    assert forecast.fomo_lost_rub > 0


def test_build_compact_digest_contains_leader_and_oos() -> None:
    products = compute_product_margins(_sample_rows())
    abc = run_abc_analysis(products)
    oos = run_out_of_stock_forecasts(products)
    digest = build_compact_digest(products, abc, oos)
    assert "WRAPPER" in digest.compact_line
    assert digest.group_a_leader == "WRAPPER"
    assert digest.oos_product == "BOX"
    assert digest.oos_days is not None


def test_no_sales_no_oos_risk() -> None:
    p = WbProductMetrics(
        sku="X",
        name="X",
        revenue=1000,
        margin=500,
        stock_qty=10,
        sales_7d_qty=0,
    )
    f = forecast_out_of_stock(p)
    assert f.risk_out_of_stock is False
    assert f.days_until_stockout is None
