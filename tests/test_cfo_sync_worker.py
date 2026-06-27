"""sync_table_cfo_processing_worker — ETL с диска и налогом из FSM."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from services.table_text_response import FINANCE_REPORT_BUILD

from services.file_processor import COLUMN_SYNONYMS, sync_table_cfo_processing_worker


def _write_wb_matrix(path: Path) -> None:
    matrix = [
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
        ["Товар", "SKU-1", "Возврат", "Возврат", "1", "600", "400", "30", "20"],
        ["—", "—", "Удержание", "Стоимость хранения", "", "", "-500", "", ""],
    ]
    wb = Workbook()
    ws = wb.active
    for row in matrix:
        ws.append(row)
    wb.save(path)
    wb.close()


def test_column_synonyms_aliases() -> None:
    assert "rrc_price" in COLUMN_SYNONYMS
    assert "продажа (ррц)" in COLUMN_SYNONYMS["rrc_price"]
    assert "баркод" in COLUMN_SYNONYMS["sku"]


def test_sync_table_cfo_processing_worker_from_xlsx() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "wb_report.xlsx"
        _write_wb_matrix(path)
        result = sync_table_cfo_processing_worker(
            str(path),
            "wildberries",
            "wb_ozon_finance",
            "USN",
            6.0,
        )
    assert result.get("cfo_build") == FINANCE_REPORT_BUILD
    assert result["tax_type"] == "USN"
    assert result["tax_rate"] == pytest.approx(6.0)
    assert result["total_revenue"] == pytest.approx(1800.0)
    assert result["tax_total"] == pytest.approx(108.0)
    assert result["total_storage_cost"] == pytest.approx(500.0)
    assert "SKU-1" in result["sku_data"]
    assert result["sku_data"]["SKU-1"]["sales_count"] == 2
    assert result["sku_data"]["SKU-1"]["returns_count"] == 1


def test_build_wb_finance_consulting_html_from_cfo_metrics() -> None:
    from services.table_wb_finance_ai import build_wb_finance_consulting_html_from_cfo_metrics

    metrics = {
        "tax_type": "USN",
        "tax_rate": 6.0,
        "total_revenue": 100_000.0,
        "tax_total": 6_000.0,
        "net_profit": 12_000.0,
        "margin_pct": 12.0,
        "total_storage_cost": 2_500.0,
        "total_system_losses": 500.0,
        "sku_data": {
            "SKU-1": {
                "sales_count": 10,
                "returns_count": 2,
                "rrc_revenue": 80_000.0,
                "payout": 60_000.0,
                "delivery": 5_000.0,
                "stock": 0,
            },
            "SKU-2": {"sales_count": 1, "returns_count": 0, "rrc_revenue": 20_000.0, "stock": 5},
        },
        "oos_zero_stock_items": ["SKU-1"],
        "oos_critical_sku": [
            {
                "sku": "SKU-2",
                "article_id": "SKU-2",
                "name": "SKU-2",
                "days": 3,
                "stock_qty": 5,
            }
        ],
    }
    html = build_wb_finance_consulting_html_from_cfo_metrics(metrics)
    assert "cfo-v12" in html
    assert "НАЛОГ USN (6%)" in html
    assert "SKU-1" in html
    assert "🔴 ТОВАР ПОЛНОСТЬЮ ЗАКОНЧИЛСЯ" in html
    assert "Срочно закупите лидера SKU-1" in html
    assert "Целевая чистая прибыль со штуки" in html
    assert "Срочно закупите: SKU-1" in html
    assert "🟡 СКОРО ЗАКОНЧИТСЯ" in html
    assert "остаток 5 шт." not in html
    assert "через 3 дн." not in html
    assert "Списания за хранение: 2 500.00 руб." in html
