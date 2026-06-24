"""Премиальное emoji-превью Wildberries."""

from __future__ import annotations

from services.table_xlsx_flow import (
    aggregate_wb_preview_products,
    build_wb_telegram_preview_html,
    infer_wb_report_period,
)


def _wb_rows() -> list[list[str]]:
    return [
        [
            "Бренд",
            "Наименование",
            "Выкупили, шт.",
            "К перечислению за товар, руб.",
        ],
        ["ACME", "Стакан керамический большой", "2", "1200.50"],
        ["ACME", "Стакан керамический большой", "3", "1800.00"],
        ["ACME", "Кружка путешественника", "1", "950.00"],
        ["ACME", "Тарелка глубокая", "4", "400.00"],
    ]


def test_aggregate_merges_duplicate_products() -> None:
    products = aggregate_wb_preview_products(_wb_rows())
    assert products is not None
    by_name = {p.name: p for p in products}
    assert by_name["Стакан керамический большой"].pcs == 5.0
    assert by_name["Стакан керамический большой"].rub == 3000.5


def test_build_wb_preview_emoji_layout() -> None:
    html = build_wb_telegram_preview_html(_wb_rows(), title="Продажи_август")
    assert html is not None
    assert "ОТЧЕТ ПО ПРОДАЖАМ WILDBERRIES" in html
    assert "Август" in html
    assert "│" not in html
    assert "<pre>" not in html
    assert "3 000.50" in html or "3000.50" in html
    assert "5" in html
    assert "Топ-5" in html
    assert html.count("🏷️") <= 5


def test_long_name_truncated_with_ellipsis() -> None:
    rows = [
        ["Наименование", "Выкупили, шт.", "К перечислению за товар, руб."],
        [
            "Очень длинное название товара которое не помещается в одну строку превью",
            "1",
            "100",
        ],
    ]
    html = build_wb_telegram_preview_html(rows, title="T")
    assert html is not None
    assert "…" in html
    assert "Очень длинное" in html
    assert "помещается" not in html


def test_infer_wb_report_period() -> None:
    assert infer_wb_report_period("wb_sales_август_2024") == "Август"
    assert infer_wb_report_period("report") == "report"
