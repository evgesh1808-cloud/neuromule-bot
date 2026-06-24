"""WB Smart Chart: группировка, TOP-7, matplotlib PNG."""

from __future__ import annotations

from services.table_wb_chart import (
    _OTHERS_LABEL,
    extract_wb_sales_series,
    render_wb_chart_from_rows,
    render_wb_sales_chart_png,
    try_render_wb_chart_png,
)


def _wb_rows() -> list[list[str]]:
    headers = [
        "Бренд",
        "Предмет",
        "Артикул продавца",
        "Баркод",
        "К перечислению за товар, руб.",
        "Выкупили, шт.",
    ]
    return [
        headers,
        ["ACME", "Футболка", "A1", "111", "12000", "30"],
        ["ACME", "Футболка", "A1", "112", "8000", "20"],
        ["ACME", "Шорты", "A2", "113", "15000", "25"],
        ["ACME", "Кепка", "A3", "114", "3000", "10"],
        ["ACME", "Носки", "A4", "115", "2000", "40"],
        ["ACME", "Рюкзак", "A5", "116", "9000", "8"],
        ["ACME", "Перчатки", "A6", "117", "1500", "12"],
        ["ACME", "Шарф", "A7", "118", "1000", "6"],
        ["ACME", "Пояс", "A8", "119", "800", "4"],
        ["ACME", "Ремень", "A9", "120", "600", "3"],
    ]


def test_extract_wb_groups_and_top7() -> None:
    series = extract_wb_sales_series(_wb_rows())
    assert series is not None
    assert "Футболка" in series.labels
    assert series.values[series.labels.index("Футболка")] == 20000.0
    assert _OTHERS_LABEL in series.labels or len(series.labels) <= 7
    assert len(series.labels) <= 8
    assert series.is_revenue is True


def test_render_wb_chart_returns_png() -> None:
    series = extract_wb_sales_series(_wb_rows())
    assert series is not None
    png = render_wb_sales_chart_png(series)
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_try_render_from_wide_wb_table() -> None:
    png = try_render_wb_chart_png(_wb_rows())
    assert png is not None
    assert len(png) > 500


def test_fallback_to_units_when_no_revenue_column() -> None:
    rows = [
        ["Предмет", "Артикул", "Выкупили, шт."],
        ["Футболка", "1", "10"],
        ["Шорты", "2", "5"],
    ]
    series = extract_wb_sales_series(rows)
    assert series is not None
    assert series.is_revenue is False
    assert "Футболка" in series.labels


def test_render_line_and_pie_chart_types() -> None:
    rows = _wb_rows()
    line_png = render_wb_chart_from_rows(rows, chart_type="line")
    pie_png = render_wb_chart_from_rows(rows, chart_type="pie")
    assert line_png is not None and line_png[:8] == b"\x89PNG\r\n\x1a\n"
    assert pie_png is not None and pie_png[:8] == b"\x89PNG\r\n\x1a\n"

