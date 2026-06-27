"""Локальный ETL товарной матрицы: ABC, FOMO логистики, OOS."""

from __future__ import annotations

import pytest

from pathlib import Path

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
    assert etl.logistics_fomo_items
    assert etl.reverse_logistics_shop_avg >= 50.0
    assert "Логистика возвратов:" in etl.logistics_fomo_items[0]
    assert "Общий убыток на пустых покатушках" in etl.return_logistics_block
    assert "обратной логистики по литражу" not in etl.return_logistics_block


def test_logistics_fomo_applies_wb_minimum_tariff() -> None:
    """Малый общий логистический расход не даёт тариф 1 ₽/шт — пол 50 ₽."""
    matrix = [
        [
            "Предмет",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
        ],
        ["CHEAP", "0", "20", "0", "20", "0"],
    ]
    etl = compute_seller_matrix_etl(matrix, revenue_total=10_000.0)
    assert etl is not None
    assert etl.logistics_fomo_rub == pytest.approx(20 * 50.0)
    assert etl.reverse_logistics_shop_avg == pytest.approx(50.0)
    assert "≈ 1 000.00 руб." in etl.logistics_fomo_items[0]
    assert "×" not in etl.logistics_fomo_items[0]


def test_logistics_fomo_dedicated_return_column() -> None:
    matrix = [
        [
            "Предмет",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "Логистика возвратов, руб.",
            "К перечислению, руб.",
        ],
        ["Стаканы", "10", "30", "5", "1000", "5000", "50000"],
    ]
    etl = compute_seller_matrix_etl(matrix, revenue_total=50_000.0)
    assert etl is not None
    # 5 фактических возвратов; 5000 / 5 = 1000 ₽/шт (выше пола 50)
    assert etl.reverse_logistics_shop_avg == pytest.approx(1000.0)
    assert "5 возвратов" in etl.logistics_fomo_items[0]
    assert "≈ 5 000.00 руб." in etl.logistics_fomo_items[0]


def test_logistics_fomo_uses_tariffs_cache_by_warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FOMO: тариф обратной логистики из JSON-кэша × литраж × возвраты."""
    from services.wb_tariffs_cache import (
        GlobalTariffsCache,
        WarehouseTariffRow,
        save_global_tariffs_cache,
    )

    cache_path = tmp_path / "GLOBAL_TARIFFS_CACHE.json"
    save_global_tariffs_cache(
        GlobalTariffsCache(
            updated_at="2026-05-27",
            source="test",
            build="test",
            warehouses={
                "коледино": WarehouseTariffRow(
                    warehouse_name="Коледино",
                    return_base_rub=50.0,
                    return_liter_rub=10.0,
                )
            },
        ),
        cache_path,
    )
    monkeypatch.setattr(
        "services.wb_tariffs_cache.default_cache_path",
        lambda: cache_path,
    )

    matrix = [
        [
            "Предмет",
            "Артикул",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Наименование склада",
            "Литраж, л",
            "К перечислению, руб.",
        ],
        ["Стаканы", "Стаканы_500шт", "0", "10", "4", "Коледино", "2", "10000"],
    ]
    etl = compute_seller_matrix_etl(matrix, revenue_total=10_000.0)
    assert etl is not None
    # unit = 50 + 10*2 = 70; loss = 4 * 70 = 280
    assert etl.logistics_fomo_rub == pytest.approx(280.0)
    assert "Коледино" in etl.logistics_fomo_items[0]
    assert "2.0 л" in etl.logistics_fomo_items[0]
    assert "кэш тарифов WB" in etl.logistics_fomo_items[0]


def test_returns_qty_capped_by_deliveries() -> None:
    """Возвраты не могут превышать доставки по SKU."""
    matrix = [
        [
            "Предмет",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
        ],
        ["BAD", "5", "10", "9999", "100", "5000"],
    ]
    etl = compute_seller_matrix_etl(matrix, revenue_total=13_195.0)
    assert etl is not None
    assert "9999" not in etl.logistics_fomo_items[0]
    assert "10 возвратов" in etl.logistics_fomo_items[0]


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
