"""JSON Mode table_generator: парсинг ответа OpenRouter."""

from __future__ import annotations

import json

from services.table_json import canonicalize_table_json, parse_table_json_response

SAMPLE_JSON = (
    '{"title":"Доход по месяцам","headers":["Месяц","Доход"],'
    '"rows":[["Янв",1200],["Фев",1500],["Мар",1800]]}'
)

SAMPLE_SHARE_JSON = (
    '{"title":"Структура","headers":["Категория","Доля"],'
    '"rows":[["Еда",40],["Транспорт",25],["Прочее",35]]}'
)


def test_parse_table_json_response() -> None:
    payload = parse_table_json_response(SAMPLE_JSON)
    assert payload is not None
    assert payload.title == "Доход по месяцам"
    assert payload.headers == ["Месяц", "Доход"]
    assert payload.rows[0] == ["Янв", "1200"]


def test_parse_strips_json_fence() -> None:
    wrapped = f"```json\n{SAMPLE_JSON}\n```"
    payload = parse_table_json_response(wrapped)
    assert payload is not None
    assert payload.headers[0] == "Месяц"


def test_canonicalize_compact_json() -> None:
    canonical = canonicalize_table_json(SAMPLE_JSON)
    assert canonical is not None
    data = json.loads(canonical)
    assert data["headers"] == ["Месяц", "Доход"]
    assert ", " not in canonical


def test_parse_invalid_returns_none() -> None:
    assert parse_table_json_response("| A | B |") is None
    assert canonicalize_table_json("not json") is None
    assert parse_table_json_response('{"title":"Rrr","headers":["A"],"rows":[]}') is None
    assert parse_table_json_response('{"title":"Rrr","headers":["A"],"rows":[["",""]]}') is None
