"""Единый парсер WB: детализация и матрица."""

from __future__ import annotations

import pytest

from services.file_processor import compute_seller_matrix_etl
from services.table_text_response import compute_wb_marketplace_metrics
from services.table_xlsx_preprocess import compute_marketplace_revenue_total
from services.wb_report_parser import detect_wb_report_kind, parse_wb_report
from tests.test_wb_transaction_parse import _weekly_matrix


def _matrix_sample() -> list[list[str]]:
    return [
        [
            "Предмет",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
            "Остаток на складе, шт.",
        ],
        ["WRAPPER", "70", "90", "5", "3500", "100000", "30"],
        ["BOX", "14", "20", "2", "800", "15000", "2000", "6"],
    ]


def test_detect_report_kinds() -> None:
    assert detect_wb_report_kind(_weekly_matrix()[0]) == "transaction"
    assert detect_wb_report_kind(_matrix_sample()[0]) == "matrix"


def test_parse_transaction_report() -> None:
    model = parse_wb_report(_weekly_matrix())
    assert model is not None
    assert model.kind == "transaction"
    assert model.sales_qty == pytest.approx(27.0)
    assert model.revenue == pytest.approx(13 * 500 + 14 * 400)
    assert model.storage_cost == pytest.approx(2782.27)
    assert model.credit_deductions == pytest.approx(15000.0)
    assert model.drr_pct < 20.0
    assert model.clear_profit < 0
    assert model.operational_profit > 0
    assert len(model.sku_by_key) == 2


def test_parse_matrix_report() -> None:
    model = parse_wb_report(_matrix_sample())
    assert model is not None
    assert model.kind == "matrix"
    assert model.revenue == pytest.approx(115_000.0)
    assert model.sales_qty == pytest.approx(84.0)
    assert model.buyout_coef_pct > 0


def test_single_entry_matches_legacy_metrics() -> None:
    matrix = _weekly_matrix()
    revenue = compute_marketplace_revenue_total(matrix)
    model = parse_wb_report(matrix)
    metrics = compute_wb_marketplace_metrics(matrix, revenue_total=revenue)
    assert model is not None and metrics is not None
    assert model.revenue == pytest.approx(revenue)
    assert model.sales_qty == pytest.approx(metrics.sales_qty)
    assert model.ad_spend == pytest.approx(metrics.total_advertising_cost)
    assert model.drr_pct == pytest.approx(metrics.ad_load_pct)


def test_etl_uses_unified_parser() -> None:
    matrix = _weekly_matrix()
    revenue = compute_marketplace_revenue_total(matrix)
    etl = compute_seller_matrix_etl(matrix, revenue_total=revenue)
    assert etl is not None
    assert len(etl.sku_catalog) == 2
    assert all(s.buyout_pct > 0 for s in etl.sku_catalog)
