"""cfo-v12 SaaS Protected: валидатор структуры WB, фильтр ID, защита SKU."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from services.file_processor import (
    WB_FINANCE_ERROR_INVALID_STRUCTURE,
    _sum_workbook_sheet_amounts,
    build_cfo_metrics_dict_from_rows,
    validate_wb_finance_detail_structure,
)
from services.table_text_response import (
    CFO_BUILD_FOOTER_PLAIN,
    is_wb_finance_invalid_structure,
)
from services.wb_transaction_parse import is_valid_wb_sku


def test_validate_wb_finance_detail_structure_accepts_official_headers() -> None:
    matrix = [
        [
            "Предмет",
            "Артикул поставщика",
            "Тип документа",
            "Обоснование для оплаты",
            "К перечислению продавцу за реализованный товар",
            "Услуги по доставке товара покупателю",
            "Склад",
        ],
        ["Товар", "SKU-1", "Продажа", "Продажа", "1000", "30", "Казань"],
    ]
    assert validate_wb_finance_detail_structure(matrix) is True


def test_validate_wb_finance_detail_structure_accepts_alternate_month_headers() -> None:
    """Другой месяц WB: без «тип документа» / длинного РРЦ — только базовые маркеры."""
    matrix = [
        [
            "Предмет",
            "Артикул поставщика",
            "Баркод",
            "Склад отгрузки",
            "Услуги по доставке",
            "Сумма к перечислению",
        ],
        ["Товар", "SKU-2", "4600123456789", "Подольск", "45", "1200"],
    ]
    assert validate_wb_finance_detail_structure(matrix) is True


def test_validate_wb_finance_detail_structure_rejects_brand_report() -> None:
    matrix = [
        ["Бренд", "BENOVY", "Выкупили", "Акция", "Итог"],
        ["WRAPPER", "12345", "500", "10", "510"],
    ]
    assert validate_wb_finance_detail_structure(matrix) is False


def test_build_cfo_metrics_invalid_structure_payload() -> None:
    matrix = [
        ["Бренд", "BENOVY", "Выручка"],
        ["WRAPPER", "1000"],
    ]
    result = build_cfo_metrics_dict_from_rows(matrix, "wildberries", "USN", 6.0)
    assert is_wb_finance_invalid_structure(result)
    assert result["error"] == WB_FINANCE_ERROR_INVALID_STRUCTURE


def test_sum_workbook_sheet_skips_barcode_amounts() -> None:
    rows = [
        ["Склад", "Стоимость хранения, руб."],
        ["208547", "2782.27"],
        ["4601234567890", "4601234567890"],
    ]
    assert _sum_workbook_sheet_amounts(rows) == pytest.approx(2782.27)


def test_is_valid_wb_sku_rejects_technical_junk() -> None:
    assert not is_valid_wb_sku("Выкупили", "Итог")
    assert not is_valid_wb_sku("123456", "")
    assert is_valid_wb_sku("Посуда", "DISH-01")


def test_cfo_build_footer_saas_protected() -> None:
    assert CFO_BUILD_FOOTER_PLAIN == "CFO build cfo-v12 (SaaS Protected Build)"


def test_check_wb_finance_upload_file_rejects_brand_xlsx() -> None:
    from services.file_processor import check_wb_finance_upload_file

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "benovy.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.append(["Бренд", "BENOVY", "Выкупили", "Дата"])
        ws.append(["WRAPPER", "SKU-X", "100", "2025-01-01"])
        wb.save(path)
        wb.close()
        probe = check_wb_finance_upload_file(str(path))

    assert is_wb_finance_invalid_structure(probe)
