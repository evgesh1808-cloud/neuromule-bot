"""Парсинг JSON-ответа роли table_generator (OpenRouter JSON Mode)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class TableJsonPayload:
    """Нормализованные табличные данные из ответа модели."""

    title: str
    headers: list[str]
    rows: list[list[str]]
    raw_json: str

    def to_rows_with_header(self) -> list[list[str]]:
        """Формат для openpyxl/matplotlib: первая строка — заголовки."""
        return [self.headers, *self.rows]


def _cell_to_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    return str(value).strip()


def _strip_json_wrappers(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = _JSON_FENCE_RE.sub("", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def table_payload_has_data(payload: TableJsonPayload) -> bool:
    """True, если в таблице есть хотя бы одна непустая ячейка данных."""
    if not payload.rows:
        return False
    for row in payload.rows:
        if any((cell or "").strip() for cell in row):
            return True
    return False


def parse_table_json_response(ai_response: str) -> TableJsonPayload | None:
    """
    Безопасно разбирает JSON-ответ модели.

    Возвращает ``None``, если JSON невалиден или структура не соответствует контракту.
    """
    try:
        blob = _strip_json_wrappers(ai_response)
        if not blob:
            return None
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None

        title = _cell_to_str(data.get("title")) or "Отчёт NeuroMule"
        headers_raw = data.get("headers")
        rows_raw = data.get("rows")
        if not isinstance(headers_raw, list) or not headers_raw:
            return None
        if not isinstance(rows_raw, list):
            return None

        headers = [_cell_to_str(h) for h in headers_raw]
        rows: list[list[str]] = []
        for row in rows_raw:
            if not isinstance(row, (list, tuple)):
                continue
            rows.append([_cell_to_str(cell) for cell in row])

        stored: dict[str, Any] = {"title": title, "headers": headers, "rows": rows}
        for key, value in data.items():
            if key not in stored:
                stored[key] = value

        payload = TableJsonPayload(
            title=title,
            headers=headers,
            rows=rows,
            raw_json=json.dumps(
                stored,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        if not table_payload_has_data(payload):
            return None
        return payload
    except Exception:
        logger.debug("parse_table_json_response failed", exc_info=True)
        return None


def canonicalize_table_json(ai_response: str) -> str | None:
    """Парсит ответ и возвращает канонический JSON для БД / Mini App API."""
    try:
        payload = parse_table_json_response(ai_response)
        return payload.raw_json if payload else None
    except Exception:
        logger.debug("canonicalize_table_json failed", exc_info=True)
        return None
