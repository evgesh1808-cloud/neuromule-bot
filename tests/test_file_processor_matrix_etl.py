"""Локальный ETL товарной матрицы: ABC, FOMO логистики, OOS."""

from __future__ import annotations

from services.file_processor import compute_seller_matrix_etl


def _sample_matrix() -> list[list[str]]:
    return [
        [
            "Предмет",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
            "Вознаграждение",
            "Остаток на складе, шт.",
        ],
        ["WRAPPER", "70", "90", "5", "3500", "100000", "8000", "30"],
        ["BOX", "14", "20", "2", "800", "15000", "2000", "6"],
        ["DEAD", "0", "10", "0", "500", "0", "400", "100"],
    ]


def test_abc_analysis_by_net_profit() -> None:
    etl = compute_seller_matrix_etl(_sample_matrix(), revenue_total=115_000.0)
    assert etl is not None
    assert etl.abc_a_leader == "WRAPPER"
    assert len(etl.abc_group_a) >= 1
    assert any(s.label == "DEAD" and s.abc_group == "C" for s in etl.abc_group_c)


def test_logistics_fomo_non_buyouts() -> None:
    etl = compute_seller_matrix_etl(_sample_matrix(), revenue_total=115_000.0)
    assert etl is not None
    assert etl.logistics_fomo_rub > 0
    assert "невыкуп" in etl.logistics_fomo_detail.lower() or "Логистика" in etl.logistics_fomo_detail


def test_sku_catalog_line_format() -> None:
    etl = compute_seller_matrix_etl(_sample_matrix(), revenue_total=115_000.0)
    assert etl is not None
    assert etl.sku_catalog
    line = etl.sku_catalog[0].catalog_line()
    assert "Артикул:" in line
    assert "руб." in line
    assert "%" in line
    assert etl.outsider_sku is not None
    assert etl.outsider_sku.name == "DEAD"


def test_oos_forecast_risky_sku() -> None:
    etl = compute_seller_matrix_etl(_sample_matrix(), revenue_total=115_000.0)
    assert etl is not None
    assert etl.oos_critical_sku is not None
    assert etl.oos_critical_days is not None
    assert etl.oos_critical_days < 7
