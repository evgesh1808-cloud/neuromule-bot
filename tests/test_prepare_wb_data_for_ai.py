"""prepare_wb_data_for_ai — MPSTATS JSON из реального WB xlsx."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.table_wb_finance_ai import build_wb_mpstats_ai_context, prepare_wb_data_for_ai


def _sample_matrix() -> list[list[str]]:
    return [
        [
            "Бренд",
            "Артикул",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
            "Остаток на складе, шт.",
        ],
        ["ACME", "SKU-A1", "70", "90", "5", "3500", "100000", "30"],
        ["DEAD", "SKU-C1", "0", "10", "0", "500", "0", "100"],
    ]


def test_build_wb_mpstats_ai_context_structure() -> None:
    ctx = build_wb_mpstats_ai_context(_sample_matrix(), revenue_total=100_000.0)
    assert "finance" in ctx
    assert ctx["finance"]["total_revenue"] == 100_000.0
    assert ctx["finance"]["tax_usn"] == pytest.approx(6_000.0)
    assert "abc_analysis" in ctx
    assert ctx["abc_analysis"]["group_A"]
    assert "problem_zones" in ctx
    assert "oos_predictions" in ctx
    assert "loss_calculator" in ctx
    assert "return_logistics" in ctx["loss_calculator"]


def test_prepare_wb_data_for_ai_from_xlsx(tmp_path: Path) -> None:
    from openpyxl import Workbook

    path = tmp_path / "wb_sample.xlsx"
    wb = Workbook()
    ws = wb.active
    for row in _sample_matrix():
        ws.append(row)
    wb.save(path)

    raw = prepare_wb_data_for_ai(path)
    data = json.loads(raw)
    assert data["finance"]["total_revenue"] == 100_000.0
    assert isinstance(data["problem_zones"]["ballast"], list)
    assert isinstance(data["problem_zones"]["non_liquid"], list)
    assert data.get("parser") == "wb_weekly_openpyxl_v1" or "loss_calculator" in data


def test_weekly_parser_caps_returns_by_orders(tmp_path: Path) -> None:
    from openpyxl import Workbook

    from services.table_wb_finance_ai import build_wb_weekly_xlsx_ai_context

    path = tmp_path / "wb_bad_returns.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(
        [
            "Бренд",
            "Артикул",
            "К перечислению, руб.",
            "Логистика, руб.",
            "Возвраты, шт.",
            "Доставки, шт.",
        ]
    )
    ws.append(["BAD", "X-1", 5000, 100, 9999, 10])
    wb.save(path)

    ctx = build_wb_weekly_xlsx_ai_context(path)
    assert ctx is not None
    sku = ctx["sku_catalog"][0]
    assert sku["returns_count"] <= sku["orders_count"]
    ballast = ctx["problem_zones"]["ballast"]
    if ballast:
        assert ballast[0]["returns"] <= 10
    lines = ctx["loss_calculator"]["return_logistics"]["lines"]
    assert lines
    assert "≈" in lines[0]["text"]
    assert "×" not in lines[0]["text"]
