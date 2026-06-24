"""Предобработка Excel-отчётов Wildberries / маркетплейсов."""

from __future__ import annotations

from services.table_xlsx_preprocess import preprocess_xlsx_rows, pick_telegram_preview_rows


def _wb_sample_rows() -> list[list[str]]:
    return [
        ["", "", ""],
        ["Отчёт по данным поставщика ООО Тест", "", ""],
        ["", "", ""],
        [
            "Бренд",
            "Предмет",
            "Артикул",
            "Баркод",
            "Контракт",
            "Заказано, шт.",
            "Выкупили, шт.",
            "К перечислению, руб.",
        ],
        [
            "Бренд",
            "Предмет",
            "Артикул",
            "Баркод",
            "Контракт",
            "Заказано, шт.",
            "Выкупили, шт.",
            "К перечислению, руб.",
        ],
        ["ACME", "Футболка", "111", "4601", "K1", "10", "8", "4000"],
        ["", ",", ""],
        ["ACME", "Шорты", "222", "4602", "K1", "5", "4", "2000"],
        ["ИТОГО", "", "", "", "", "15", "12", "6000"],
    ]


def test_preprocess_skips_service_rows_and_finds_header() -> None:
    pre = preprocess_xlsx_rows(_wb_sample_rows(), title="WB")
    assert pre.rows[0][0] == "Бренд"
    assert len(pre.rows) == 3  # header + 2 data rows
    assert pre.rows[-1][0] == "ACME"
    assert pre.summary is not None
    assert "ИТОГО" in pre.summary
    assert "6000" in pre.summary


def test_preprocess_filters_mostly_empty_rows() -> None:
    pre = preprocess_xlsx_rows(_wb_sample_rows(), title="WB")
    bodies = [row[0] for row in pre.rows[1:]]
    assert "" not in bodies
    assert "," not in bodies


def test_telegram_preview_keeps_key_columns_only() -> None:
    pre = preprocess_xlsx_rows(_wb_sample_rows(), title="WB")
    assert len(pre.rows[0]) == 8
    assert len(pre.telegram_rows[0]) <= 5
    headers = " ".join(pre.telegram_rows[0]).lower()
    assert "бренд" in headers
    assert "заказано" in headers or "выкупили" in headers
    assert "баркод" not in headers


def test_pick_telegram_preview_narrow_table_unchanged() -> None:
    rows = [["A", "B"], ["1", "2"]]
    assert pick_telegram_preview_rows(rows) == rows


def test_title_includes_summary_when_short() -> None:
    pre = preprocess_xlsx_rows(_wb_sample_rows(), title="Продажи WB")
    assert "Продажи WB" in pre.title
    assert "ИТОГО" in pre.title


def _wb_rows_with_num_prefix() -> list[list[str]]:
    return [
        ["Отчёт по данным поставщика", "", ""],
        [
            "№",
            "Бренд",
            "Предмет",
            "Артикул продавца",
            "К перечислению Продавцу за реализованный Товар",
        ],
        ["1", "ACME", "Футболка", "111", "4000"],
        ["2", "ACME", "Шорты", "222", "2000"],
        ["ИТОГО", "", "", "", "6000"],
    ]


def test_preprocess_finds_header_with_num_prefix_column() -> None:
    pre = preprocess_xlsx_rows(_wb_rows_with_num_prefix(), title="WB")
    assert pre.rows
    assert pre.rows[0][0] == "№"
    assert "перечислению" in pre.rows[0][-1].lower()
    assert len(pre.rows) == 3
    assert pre.revenue_total == 6000.0


def test_compute_marketplace_revenue_total() -> None:
    from services.table_xlsx_preprocess import compute_marketplace_revenue_total

    pre = preprocess_xlsx_rows(_wb_sample_rows(), title="WB")
    total = compute_marketplace_revenue_total(pre.rows)
    assert total == 6000.0
    assert pre.revenue_total == 6000.0
