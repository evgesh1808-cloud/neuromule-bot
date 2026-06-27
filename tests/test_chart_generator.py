"""chart_generator: rrc_revenue barh, форматирование, синхрон с CFO metrics."""

from __future__ import annotations

from services.chart_generator import (
    HorizontalBarSeries,
    build_rrc_chart_series_from_final_metrics,
    build_rrc_chart_series_from_rows,
    build_rrc_chart_series_from_sku_data,
    format_rub_thousands,
    render_horizontal_bar_chart_png,
)
from services.file_processor import build_cfo_metrics_dict_from_rows


def _cfo_matrix_rows() -> list[list[str]]:
    return [
        [
            "Предмет",
            "Артикул поставщика",
            "Тип документа",
            "Обоснование для оплаты",
            "Кол-во",
            "Продажа (РРЦ)",
            "К перечислению продавцу за реализованный товар",
            "Услуги по доставке товара покупателю",
            "Вознаграждение Вайлдберриз",
        ],
        ["Товар", "SKU-1", "Продажа", "Продажа", "2", "2400", "1600", "100", "50"],
        ["Товар", "SKU-2", "Продажа", "Продажа", "1", "5000", "4000", "80", "40"],
        ["Товар", "SKU-1", "Возврат", "Возврат", "1", "600", "400", "30", "20"],
    ]


def test_format_rub_thousands() -> None:
    assert format_rub_thousands(1_234_567.8) == "1 234 568"
    assert format_rub_thousands(0) == "0"


def test_build_rrc_series_from_sku_data_matches_metrics() -> None:
    metrics = {
        "sku_data": {
            "SKU-1": {"rrc_revenue": 80_000.0},
            "SKU-2": {"rrc_revenue": 20_000.0},
        }
    }
    series = build_rrc_chart_series_from_final_metrics(metrics)
    assert series is not None
    assert series.labels == ["SKU-1", "SKU-2"]
    assert series.values == [80_000.0, 20_000.0]


def test_build_rrc_series_from_rows_uses_cfo_rrc_revenue() -> None:
    rows = _cfo_matrix_rows()
    metrics = build_cfo_metrics_dict_from_rows(rows, "wildberries", "USN", 6.0)
    assert not metrics.get("error")

    sku_data = metrics["sku_data"]
    series = build_rrc_chart_series_from_rows(rows, tax_type="USN", tax_rate=6.0)
    assert series is not None

    expected_by_sku = {
        sku: round(float(stats["rrc_revenue"]), 2)
        for sku, stats in sku_data.items()
        if float(stats.get("rrc_revenue", 0)) > 0
    }
    chart_sum = round(sum(series.values), 2)
    metrics_sum = round(sum(expected_by_sku.values()), 2)
    assert chart_sum == metrics_sum

    top_label, top_value = series.labels[0], series.values[0]
    top_sku = max(expected_by_sku, key=expected_by_sku.get)
    assert top_label == top_sku
    assert top_value == expected_by_sku[top_sku]


def test_render_horizontal_bar_chart_png() -> None:
    series = HorizontalBarSeries(
        labels=["SKU-1", "SKU-2"],
        values=[80_000.0, 20_000.0],
    )
    png = render_horizontal_bar_chart_png(series)
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 500


def test_build_rrc_series_from_sku_data_top7_and_others() -> None:
    sku_data = {f"SKU-{i}": {"rrc_revenue": float(i * 1000)} for i in range(1, 11)}
    series = build_rrc_chart_series_from_sku_data(sku_data)
    assert series is not None
    assert len(series.labels) == 8
    assert series.labels[-1] == "Другие"
    assert series.values[0] == 10_000.0
