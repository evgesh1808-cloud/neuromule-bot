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
    from services.table_wb_finance_ai import (
        WbFinancePromptMetrics,
        build_wb_finance_express_html_local,
    )

    metrics = WbFinancePromptMetrics(
        revenue=100_000.0,
        tax=6_000.0,
        clear_profit=12_000.0,
        adv_load_pct=10.0,
        buy_ratio_pct=80.0,
        year_forecast=1_200_000.0,
        profitability_pct=12.0,
        business_score=7.5,
        verdict="Стабильная база при риске перерасхода на рекламу и просадки кассы.",
        fomo_lost_rub=3_000.0,
        fomo_breakdown=("Списания за хранение: 2 500.00 руб.",),
        storage_cost=2_500.0,
        total_system_losses=500.0,
        abc_a_leader_name="SKU-1",
        abc_a_leader_article="SKU-1",
        top_regions=("Карелия",),
        top_warehouses=("Рязань",),
        canceled_skus=("SKU-1",),
    )
    html = build_wb_finance_express_html_local(metrics, None)
    assert "SaaS Protected Build" in html
    assert "НАЛОГ УСН" in html
    assert "SKU-1" in html
    assert "ПРОГНОЗ И ОБНУЛЕНИЕ ОСТАТКОВ" not in html
    assert "Контроль Cash Flow" in html or "Спасение Cash Flow" in html
    assert "ОПЕРАЦИОННЫЙ АУДИТ ПОСТАВОК" in html
    assert "отмены заказов" in html
    assert "СТРУКТУРА ИЗДЕРЖЕК" in html
