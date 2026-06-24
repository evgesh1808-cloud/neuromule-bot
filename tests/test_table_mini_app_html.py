"""Премиальный HTML шаблон Mini App для table_generator."""

from __future__ import annotations

from services.table_mini_app_html import build_mini_app_table_html


def test_mini_app_html_has_search_and_pagination() -> None:
    rows = [["Товар", "Выручка"], ["Стакан", "1200"], ["Кружка", "950"]]
    html = build_mini_app_table_html(rows, title="Продажи")
    assert 'id="search"' in html
    assert "pager-btn" in html
    assert "PAGE_SIZE = 15" in html
    assert "telegram-web-app.js" in html


def test_mini_app_html_numeric_column_class() -> None:
    rows = [["Месяц", "Доход"], ["Янв", "1200"]]
    html = build_mini_app_table_html(rows)
    assert "NUMERIC_COLS" in html
    assert "class=\"num\"" in html or 'class="num"' in html
