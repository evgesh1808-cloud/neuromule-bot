"""Smart Chart, Excel, кэш сессий table_generator (JSON pipeline)."""

from __future__ import annotations

from io import BytesIO

import pytest

from services.table_chart_types import ChartType
from services.table_generator_pack import (
    build_table_generator_pack,
    suggest_chart_type,
)
from services.table_json import parse_table_json_response
from services.table_markdown import rows_to_markdown_table

SAMPLE_TIME_JSON = (
    '{"title":"Доход","headers":["Месяц","Доход"],'
    '"rows":[["Янв",1200],["Фев",1500],["Мар",1800],["Апр",2100]]}'
)

SAMPLE_YEARS_JSON = (
    '{"title":"Выручка","headers":["Год","Сумма"],'
    '"rows":[["2021",100],["2022",120],["2023",140]]}'
)

SAMPLE_SHARE_JSON = (
    '{"title":"Структура","headers":["Категория","Доля"],'
    '"rows":[["Еда",40],["Транспорт",25],["Прочее",35]]}'
)


def _rows_from_json(blob: str) -> list[list[str]]:
    payload = parse_table_json_response(blob)
    assert payload is not None
    return payload.to_rows_with_header()


def test_suggest_chart_bar_for_categories() -> None:
    rows = _rows_from_json(SAMPLE_SHARE_JSON)
    assert suggest_chart_type(rows, context_text="структура расходов") is ChartType.BAR


def test_suggest_chart_line_for_month_header() -> None:
    rows = _rows_from_json(SAMPLE_TIME_JSON)
    assert suggest_chart_type(rows) is ChartType.LINE


def test_suggest_chart_line_for_sequential_years() -> None:
    rows = _rows_from_json(SAMPLE_YEARS_JSON)
    assert suggest_chart_type(rows) is ChartType.LINE


def test_build_pack_uses_suggested_chart_type() -> None:
    pack = build_table_generator_pack(SAMPLE_SHARE_JSON, context_text="доля расходов")
    assert pack is not None
    assert pack.chart_type is ChartType.BAR
    assert pack.chart_png_bytes is not None


def test_rows_to_markdown_roundtrip() -> None:
    rows = _rows_from_json(SAMPLE_TIME_JSON)
    md = rows_to_markdown_table(rows)
    assert "Месяц" in md
    assert "Янв" in md


def test_read_xlsx_rows_from_bytes() -> None:
    from openpyxl import Workbook

    from services.file_processor import read_xlsx_rows_from_bytes

    wb = Workbook()
    ws = wb.active
    ws.append(["A", "B"])
    ws.append([1, 2])
    buf = BytesIO()
    wb.save(buf)
    rows = read_xlsx_rows_from_bytes(buf.getvalue())
    assert rows[0] == ["A", "B"]
    assert rows[1] == ["1", "2"]
