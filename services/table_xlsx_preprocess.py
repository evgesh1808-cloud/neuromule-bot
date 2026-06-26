"""Предобработка сырых строк Excel (Wildberries и др.) перед отчётом."""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.table_markdown import normalize_table_rows
from services.table_number_parse import safe_float

_HEADER_MARKERS: frozenset[str] = frozenset(
    {
        "бренд",
        "предмет",
        "артикул",
        "номенклатур",
        "баркод",
        "наименование",
        "перечислению",
        "выручка",
        "реализован",
        "заказано",
        "выкупили",
        "контракт",
        "продавц",
    }
)
_B2B_MARKER_PAIRS: tuple[tuple[str, str], ...] = (
    ("предмет", "артикул"),
    ("номенклатур", "бренд"),
    ("перечислению", "выручка"),
    ("перечислению", "реализован"),
    ("бренд", "артикул"),
    ("предмет", "бренд"),
)
_REVENUE_COLUMN_PATTERNS: tuple[str, ...] = (
    "к перечислению продавцу за реализованный товар",
    "к перечислению",
    "выручка",
    "заработок",
    "перечислению",
    "итого",
)
_SERVICE_PHRASES: tuple[str, ...] = (
    "отчёт по данным поставщика",
    "отчет по данным поставщика",
    "данным поставщика",
)
_TOTAL_PREFIXES: tuple[str, ...] = ("итого", "всего", "total")

# Приоритет колонок для узкого Telegram-превью (полный Excel не трогаем).
_TELEGRAM_COLUMN_RULES: tuple[tuple[str, ...], ...] = (
    ("бренд",),
    ("наименование",),
    ("предмет",),
    ("название",),
    ("заказано",),
    ("выкупили",),
    ("перечислению",),
    ("выручка",),
    ("сумма",),
)

_EMPTY_CELL_RE = re.compile(r"^[\s,;.\-—–]*$")
_MULTI_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class XlsxPreprocessedTable:
    """Очищенная матрица + узкое превью для Telegram."""

    rows: list[list[str]]
    telegram_rows: list[list[str]]
    title: str
    summary: str | None = None
    revenue_total: float = 0.0


def _clean_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value).replace("\u00a0", " ").strip()
    text = _MULTI_SPACE_RE.sub(" ", text)
    return text


def _clean_rows(rows: list[list[str]]) -> list[list[str]]:
    cleaned = [[_clean_cell(c) for c in row] for row in rows]
    return normalize_table_rows(cleaned)


def _cell_is_empty(cell: str) -> bool:
    return not cell or bool(_EMPTY_CELL_RE.match(cell))


def _row_empty_ratio(row: list[str]) -> float:
    if not row:
        return 1.0
    empty = sum(1 for cell in row if _cell_is_empty(cell))
    return empty / len(row)


def _normalize_header_for_match(header: str) -> str:
    """Нормализация заголовка для поиска колонок (без учёта № и кавычек)."""
    text = (header or "").replace("\u00a0", " ").strip()
    text = text.replace('"', "").replace("'", "").replace("«", "").replace("»", "")
    text = _MULTI_SPACE_RE.sub(" ", text).lower()
    return text


def find_revenue_column_index(headers: list[str]) -> int | None:
    """Индекс колонки выручки WB/Ozon (приоритет — длинные фразы)."""
    lowered = [_normalize_header_for_match(h) for h in headers]
    for pattern in _REVENUE_COLUMN_PATTERNS:
        for idx, header in enumerate(lowered):
            if header and pattern in header:
                return idx
    return None


def compute_marketplace_revenue_total(rows: list[list[str]]) -> float:
    """Локальная сумма по колонке «К перечислению…» без OpenRouter."""
    from services.wb_report_parser import parse_wb_report

    matrix = normalize_table_rows(rows)
    if len(matrix) < 2:
        return 0.0
    model = parse_wb_report(matrix)
    if model is not None and model.revenue > 0:
        return model.revenue
    col = find_revenue_column_index(matrix[0])
    if col is None:
        return 0.0
    total = 0.0
    for row in matrix[1:]:
        label = (row[0] if row else "").strip().lower()
        if label.startswith("итого") or label.startswith("всего"):
            continue
        if col < len(row):
            val = safe_float(row[col])
            if val > 0:
                total += val
    return total


def _row_normalized_blob(row: list[str]) -> str:
    parts: list[str] = []
    for cell in row:
        norm = _normalize_header_for_match(_clean_cell(cell))
        if norm:
            parts.append(norm)
    return " ".join(parts)


def _header_keyword_hits(row: list[str]) -> int:
    blob = _row_normalized_blob(row)
    if not blob:
        return 0
    return sum(1 for marker in _HEADER_MARKERS if marker in blob)


def _has_b2b_marker_pair(blob: str) -> bool:
    return any(a in blob and b in blob for a, b in _B2B_MARKER_PAIRS)


def _is_probable_header_row(row: list[str]) -> bool:
    blob = _row_normalized_blob(row)
    if not blob:
        return False
    if _has_b2b_marker_pair(blob):
        return True
    hits = _header_keyword_hits(row)
    if hits >= 2:
        return True
    if hits == 1 and _row_empty_ratio(row) < 0.5:
        non_empty = sum(1 for c in row if not _cell_is_empty(c))
        return non_empty >= 3
    return False


def _is_service_preamble_row(row: list[str]) -> bool:
    if _row_empty_ratio(row) >= 1.0:
        return True
    blob = _row_normalized_blob(row)
    if not blob:
        return True
    return any(phrase in blob for phrase in _SERVICE_PHRASES)


def _row_text_blob(row: list[str]) -> str:
    return _row_normalized_blob(row)


def _find_header_row_index(rows: list[list[str]]) -> int | None:
    for idx, row in enumerate(rows):
        if _is_probable_header_row(row):
            return idx
    for idx, row in enumerate(rows):
        if _is_service_preamble_row(row):
            continue
        if sum(1 for c in row if not _cell_is_empty(c)) >= 2:
            return idx
    return None


def _is_duplicate_header_row(row: list[str], headers: list[str]) -> bool:
    if not headers:
        return False
    matches = 0
    compared = 0
    for idx, header in enumerate(headers):
        if not header:
            continue
        compared += 1
        cell = row[idx] if idx < len(row) else ""
        if cell.lower() == header.lower():
            matches += 1
    return compared >= 2 and matches >= max(2, compared // 2)


def _is_totals_row(row: list[str]) -> bool:
    first_non_empty = next((c for c in row if not _cell_is_empty(c)), "")
    if not first_non_empty:
        return False
    low = first_non_empty.lower()
    if any(low.startswith(prefix) for prefix in _TOTAL_PREFIXES):
        return True
    blob = _row_text_blob(row)
    return blob.startswith("итого") or blob.startswith("всего")


def _format_totals_summary(row: list[str], headers: list[str]) -> str:
    parts: list[str] = []
    for idx, header in enumerate(headers):
        if idx >= len(row):
            break
        cell = row[idx]
        if _cell_is_empty(cell) or _cell_is_empty(header):
            continue
        parts.append(f"{header}: {cell}")
    if parts:
        return "ИТОГО — " + "; ".join(parts[:6])
    joined = " | ".join(c for c in row if not _cell_is_empty(c))
    return f"ИТОГО — {joined}" if joined else "ИТОГО"


def _pick_telegram_column_indices(headers: list[str]) -> list[int]:
    indices: list[int] = []
    used: set[int] = set()
    lowered = [h.lower() for h in headers]

    for patterns in _TELEGRAM_COLUMN_RULES:
        for idx, header in enumerate(lowered):
            if idx in used or not header:
                continue
            if any(pattern in header for pattern in patterns):
                indices.append(idx)
                used.add(idx)
                break

    if len(indices) >= 2:
        return indices[:5]

    # Fallback: первые 5 непустых заголовков.
    for idx, header in enumerate(headers):
        if idx in used or _cell_is_empty(header):
            continue
        indices.append(idx)
        if len(indices) >= 5:
            break
    return indices


def pick_telegram_preview_rows(rows: list[list[str]]) -> list[list[str]]:
    """Узкая таблица (4–5 колонок) для caption в Telegram; Excel остаётся полным."""
    rows = _clean_rows(rows)
    if not rows:
        return []
    headers = rows[0]
    if len(headers) <= 5:
        return rows
    indices = _pick_telegram_column_indices(headers)
    if len(indices) < 2:
        indices = list(range(min(5, len(headers))))

    def project(row: list[str]) -> list[str]:
        return [_clean_cell(row[i] if i < len(row) else "") for i in indices]

    return [project(headers), *[project(row) for row in rows[1:]]]


def preprocess_xlsx_rows(
    rows: list[list[str]],
    *,
    title: str = "Отчёт NeuroMule",
) -> XlsxPreprocessedTable:
    """
    Очистка WB/маркетплейс-отчётов: служебная шапка, пустые строки, ИТОГО, превью-колонки.
    """
    cleaned = _clean_rows(rows)
    if not cleaned:
        return XlsxPreprocessedTable(
            rows=[],
            telegram_rows=[],
            title=_clean_cell(title) or "Отчёт NeuroMule",
        )

    header_idx = _find_header_row_index(cleaned)
    if header_idx is None:
        header_idx = 0

    headers = [_clean_cell(c) for c in cleaned[header_idx]]
    if not any(headers):
        return XlsxPreprocessedTable(
            rows=[],
            telegram_rows=[],
            title=_clean_cell(title) or "Отчёт NeuroMule",
        )

    data_rows: list[list[str]] = []
    summaries: list[str] = []

    for row in cleaned[header_idx + 1 :]:
        row = [_clean_cell(c) for c in row]
        if _is_duplicate_header_row(row, headers):
            continue
        if _row_empty_ratio(row) >= 0.8:
            continue
        if _is_totals_row(row):
            summaries.append(_format_totals_summary(row, headers))
            continue
        if not any(not _cell_is_empty(c) for c in row):
            continue
        data_rows.append(row)

    matrix = normalize_table_rows([headers, *data_rows])
    telegram_rows = pick_telegram_preview_rows(matrix)
    revenue_total = compute_marketplace_revenue_total(matrix)

    display_title = _clean_cell(title) or "Отчёт NeuroMule"
    summary: str | None = None
    if summaries:
        summary = summaries[0]
        if len(summaries) > 1:
            summary = f"{summary} (+{len(summaries) - 1})"
        if summary and len(summary) < 120:
            display_title = f"{display_title} · {summary}"

    return XlsxPreprocessedTable(
        rows=matrix,
        telegram_rows=telegram_rows,
        title=display_title,
        summary=summary,
        revenue_total=revenue_total,
    )
