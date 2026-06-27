"""Предупреждение о структуре колонок — только при отсутствии SKU/выручки."""

from __future__ import annotations

from services.file_processor import (
    resolve_wb_cfo_core_column_indices,
    should_warn_column_structure,
    wb_core_finance_columns_recognized,
)


def test_wb_transaction_matrix_core_columns_recognized() -> None:
    matrix = [
        [
            "Предмет",
            "Артикул поставщика",
            "Тип документа",
            "Кол-во",
            "Продажа (РРЦ)",
            "К перечислению продавцу за реализованный товар",
        ],
        ["Товар", "SKU-1", "Продажа", "2", "2400", "1600"],
    ]
    assert wb_core_finance_columns_recognized(matrix)
    assert not should_warn_column_structure(matrix, revenue_total=0.0)


def test_no_warning_when_only_sales_no_penalties() -> None:
    """Нет штрафов/возвратов в теле — warning не нужен."""
    matrix = [
        ["Предмет", "Выкупили, шт.", "К перечислению, руб."],
        ["Стаканы", "10", "50000"],
    ]
    assert wb_core_finance_columns_recognized(matrix)
    assert not should_warn_column_structure(matrix, revenue_total=0.0)


def test_resolve_core_indices_transaction_report() -> None:
    headers = [
        "Предмет",
        "Артикул поставщика",
        "Продажа (РРЦ)",
        "К перечислению продавцу за реализованный товар",
    ]
    idx_sku, idx_rrc = resolve_wb_cfo_core_column_indices(headers)
    assert idx_sku is not None
    assert idx_rrc is not None


def test_warn_when_headers_unrecognized() -> None:
    matrix = [
        ["col_a", "col_b"],
        ["1", "2"],
    ]
    assert not wb_core_finance_columns_recognized(matrix)
    assert should_warn_column_structure(matrix, revenue_total=0.0)
