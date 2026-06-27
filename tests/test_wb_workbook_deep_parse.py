"""Глубокий многолистовой парсинг WB xlsx: Хранение, Удержания, маппинг складов."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from services.file_processor import (
    collect_supply_chain_audit_from_rows,
    load_cfo_workbook_from_path,
    map_wb_warehouse_label,
    sync_table_cfo_processing_worker,
)


def _detail_headers() -> list[str]:
    return [
        "Предмет",
        "Артикул поставщика",
        "Склад",
        "Тип документа",
        "Обоснование для оплаты",
        "Кол-во",
        "Продажа (РРЦ)",
        "К перечислению продавцу за реализованный товар",
        "Услуги по доставке товара покупателю",
        "Вознаграждение Вайлдберриз",
    ]


def _write_multi_sheet_wb(path: Path) -> None:
    wb = Workbook()
    ws_detail = wb.active
    ws_detail.title = "Детализация"
    ws_detail.append(_detail_headers())
    ws_detail.append(
        ["Товар", "SKU-1", "208547", "Продажа", "Продажа", "2", "2400", "1600", "100", "50"]
    )

    ws_storage = wb.create_sheet("Хранение")
    ws_storage.append(["Склад", "Сумма"])
    ws_storage.append(["208547", "2782.27"])

    ws_hold = wb.create_sheet("Удержания")
    ws_hold.append(["Описание", "К удержанию"])
    ws_hold.append(["Кредит WB", "8192.77"])

    wb.save(path)
    wb.close()


def test_map_wb_warehouse_label() -> None:
    assert map_wb_warehouse_label("208547") == "Рязань (Тюшевское)"
    assert map_wb_warehouse_label("50003969") == "Подольск (Транзит WB)"
    assert map_wb_warehouse_label("Казань") == "Казань"


def test_load_cfo_workbook_aux_sheets() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "wb_multi.xlsx"
        _write_multi_sheet_wb(path)
        loaded = load_cfo_workbook_from_path(str(path))

    assert len(loaded.matrix) >= 2
    assert loaded.aux_storage_cost == pytest.approx(2782.27)
    assert loaded.aux_system_losses == pytest.approx(8192.77)


def test_sync_worker_picks_aux_sheet_costs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "wb_multi.xlsx"
        _write_multi_sheet_wb(path)
        result = sync_table_cfo_processing_worker(
            str(path),
            "wildberries",
            "wb_ozon_finance",
            "USN",
            6.0,
        )

    assert result["total_storage_cost"] == pytest.approx(2782.27)
    assert result["total_system_losses"] == pytest.approx(8192.77)
    assert result["top_warehouses"] == ["Рязань (Тюшевское)"]


def test_supply_chain_warehouse_id_mapping() -> None:
    matrix = [
        _detail_headers(),
        ["Товар", "SKU-1", "50003969", "Продажа", "Продажа", "1", "1200", "800", "50", "30"],
        ["Товар", "SKU-2", "208547", "Продажа", "Продажа", "1", "900", "600", "40", "20"],
    ]
    audit = collect_supply_chain_audit_from_rows(matrix)
    assert "Подольск (Транзит WB)" in audit["top_warehouses"]
    assert "Рязань (Тюшевское)" in audit["top_warehouses"]
