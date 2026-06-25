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
    assert "localization_index" in ctx


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
