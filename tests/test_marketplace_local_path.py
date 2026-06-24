"""Проверка: при revenue_total=0 OpenRouter не нужен — только Fast-Path."""

from __future__ import annotations

from services.table_xlsx_flow import marketplace_requires_local_path


def test_marketplace_requires_local_path_empty() -> None:
    force, pre = marketplace_requires_local_path([], title="Rrr")
    assert force is True
    assert not pre.rows


def test_marketplace_requires_local_path_with_revenue() -> None:
    rows = [
        ["Бренд", "К перечислению, руб."],
        ["ACME", "1000"],
        ["BETA", "500"],
    ]
    force, pre = marketplace_requires_local_path(rows, title="WB")
    assert force is False
    assert pre.revenue_total == 1500.0
