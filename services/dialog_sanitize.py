"""Сжатие истории диалога для чата (Excel-JSON не должен забивать контекст)."""

from __future__ import annotations

import json

_DIALOG_MAX_CHARS = 3500


def compact_table_history_note(
    *,
    title: str = "Отчёт NeuroMule",
    row_count: int = 0,
    table_subrole: str | None = None,
) -> str:
    from services.table_subrole_types import normalize_table_subrole

    name = (title or "Отчёт NeuroMule").strip()[:120]
    sub = normalize_table_subrole(table_subrole)
    if sub == "wb_ozon_finance":
        return (
            f"📊 Финансовый аудит WB/Ozon «{name}» ({row_count} строк). "
            "Подробный разбор — в сообщении отчёта и 📱 Studio."
        )
    return f"📊 Обработана таблица «{name}» ({row_count} строк). Детали — в отчёте и 📱 Studio."


def _compact_json_table_blob(raw: str) -> str | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if "headers" not in data and "rows" not in data:
        return None
    title = str(data.get("title") or "Excel")
    rows = data.get("rows")
    row_count = len(rows) if isinstance(rows, list) else int(data.get("row_count") or 0)
    return compact_table_history_note(title=title, row_count=row_count)


def compact_table_history_from_json(
    table_json: str,
    *,
    table_subrole: str | None = None,
) -> str:
    compact = _compact_json_table_blob(table_json)
    if compact:
        return compact
    return compact_table_history_note(table_subrole=table_subrole)


def sanitize_dialog_content_for_chat(content: str, *, max_chars: int = _DIALOG_MAX_CHARS) -> str:
    """Убирает из истории чата гигантские JSON-дампы таблиц (legacy + fast-path)."""
    s = (content or "").strip()
    if not s:
        return s
    if s.startswith("{"):
        compact = _compact_json_table_blob(s)
        if compact:
            return compact
    if len(s) > max_chars:
        return s[: max_chars - 1] + "…"
    return s
