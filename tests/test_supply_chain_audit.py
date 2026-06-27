"""Операционный аудит поставок: регионы, склады, отмены."""

from __future__ import annotations

from services.file_processor import collect_supply_chain_audit_from_rows


def test_collect_supply_chain_audit_regions_warehouses_and_cancels() -> None:
    matrix = [
        [
            "Артикул",
            "Тип документа",
            "Склад отгрузки",
            "Регион доставки",
            "К перечислению, руб.",
        ],
        ["SKU-A", "Продажа", "Рязань", "Карелия", "1000"],
        ["SKU-A", "Продажа", "Рязань", "Краснодар", "1200"],
        ["SKU-B", "Продажа", "Тула", "Алтай", "900"],
        ["SKU-B", "Продажа", "Тула", "Карелия", "800"],
        ["SKU-C", "Возврат", "Тула", "Алтай", "100"],
        ["SKU-D", "Сторно", "Рязань", "Краснодар", "50"],
    ]
    audit = collect_supply_chain_audit_from_rows(matrix)
    assert audit["top_regions"] == ["Карелия", "Краснодар", "Алтай"]
    assert audit["top_warehouses"] == ["Рязань", "Тула"]
    assert "SKU-C" in audit["canceled_skus"]
    assert "SKU-D" in audit["canceled_skus"]


def test_collect_supply_chain_audit_empty_matrix() -> None:
    audit = collect_supply_chain_audit_from_rows([])
    assert audit["top_regions"] == []
    assert audit["top_warehouses"] == []
    assert audit["canceled_skus"] == []
