"""Локальная сборка отчёта table_generator: HTML, Excel, Smart Chart (без OpenRouter)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from io import BytesIO

from services.table_chart_types import ChartType
from services.table_json import TableJsonPayload, parse_table_json_response
from services.table_markdown import (
    normalize_table_rows,
)
from services.telegram_safe_text import _escape_telegram_html, repair_telegram_html

logger = logging.getLogger(__name__)

TABLE_XLSX_FILENAME = "Отчет_Нейросеть.xlsx"
_CAPTION_MAX = 1020

_TIME_AXIS_KEYWORDS: frozenset[str] = frozenset(
    {
        "год",
        "год.",
        "месяц",
        "квартал",
        "дата",
        "время",
        "период",
        "quarter",
        "year",
        "month",
        "date",
    }
)


@dataclass(frozen=True)
class TableGeneratorPack:
    rows: list[list[str]]
    html_document: str
    telegram_caption_html: str
    xlsx_bytes: bytes
    chart_png_bytes: bytes | None
    chart_type: ChartType


def _escape_html_cell(text: str) -> str:
    return _escape_telegram_html((text or "").strip())


def markdown_table_to_html_document(rows: list[list[str]]) -> str:
    rows = normalize_table_rows(rows)
    if not rows:
        return ""
    parts = [
        '<table border="1" cellpadding="6" cellspacing="0">',
        "<thead><tr>",
    ]
    for cell in rows[0]:
        parts.append(f"<th><b>{_escape_html_cell(cell)}</b></th>")
    parts.append("</tr></thead><tbody>")
    for row in rows[1:]:
        parts.append("<tr>")
        for cell in row:
            parts.append(f"<td>{_escape_html_cell(cell)}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def markdown_table_to_telegram_caption(
    rows: list[list[str]],
    *,
    title: str | None = None,
) -> str:
    rows = normalize_table_rows(rows)
    if not rows:
        heading = _escape_telegram_html(title or "Отчёт NeuroMule")
        return f"<b>📊 {heading}</b>"
    ncols = len(rows[0])
    widths = [max(len(rows[r][c]) for r in range(len(rows))) for c in range(ncols)]

    def render_row(cells: list[str]) -> str:
        padded = [cells[c].ljust(widths[c]) if c < len(cells) else "".ljust(widths[c]) for c in range(ncols)]
        return " │ ".join(padded)

    lines = [render_row(rows[0])]
    if len(rows) > 1:
        lines.append("─" * (sum(widths) + 3 * max(ncols - 1, 0)))
        for row in rows[1:]:
            lines.append(render_row(row))
    body = _escape_telegram_html("\n".join(lines))
    heading = _escape_telegram_html(title or "Отчёт NeuroMule")
    return repair_telegram_html(f"<b>📊 {heading}</b>\n<pre>{body}</pre>")


def _parse_number(raw: str) -> float | None:
    text = (raw or "").strip()
    if not text:
        return None
    text = text.replace("\u00a0", " ").replace(" ", "")
    text = re.sub(r"[₽$€%]", "", text)
    if "," in text and "." in text:
        text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    text = re.sub(r"[^\d.\-]", "", text)
    if not text or text in {".", "-", "-."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _detect_chart_axes(rows: list[list[str]]) -> tuple[int, int] | None:
    rows = normalize_table_rows(rows)
    if len(rows) < 2:
        return None
    headers, data = rows[0], rows[1:]
    ncols = len(headers)
    numeric_cols: list[int] = []
    for col in range(ncols):
        nums = [_parse_number(row[col] if col < len(row) else "") for row in data]
        valid = [n for n in nums if n is not None]
        if len(valid) >= max(2, len(data) // 2):
            numeric_cols.append(col)
    if not numeric_cols:
        return None
    label_candidates = [c for c in range(ncols) if c not in numeric_cols]
    label_col = label_candidates[0] if label_candidates else 0
    value_col = numeric_cols[0] if numeric_cols[0] != label_col else numeric_cols[-1]
    if label_col == value_col:
        return None
    return label_col, value_col


def _extract_series(rows: list[list[str]]) -> tuple[list[str], list[float], str] | None:
    axes = _detect_chart_axes(rows)
    if axes is None:
        return None
    label_col, value_col = axes
    rows = normalize_table_rows(rows)
    labels: list[str] = []
    values: list[float] = []
    for row in rows[1:]:
        label = (row[label_col] if label_col < len(row) else "").strip() or "—"
        num = _parse_number(row[value_col] if value_col < len(row) else "")
        if num is None:
            continue
        labels.append(label[:40])
        values.append(num)
    if len(values) < 2:
        return None
    header = rows[0][value_col] if value_col < len(rows[0]) else "Значение"
    return labels, values, header


def _contains_time_axis_keyword(*texts: str) -> bool:
    """Ищет временные маркеры в заголовках / подписях оси (регистронезависимо)."""
    for raw in texts:
        low = (raw or "").strip().lower()
        if not low:
            continue
        for keyword in _TIME_AXIS_KEYWORDS:
            if keyword in low:
                return True
    return False


def _first_column_values(rows: list[list[str]]) -> list[str]:
    rows = normalize_table_rows(rows)
    if len(rows) < 2:
        return []
    return [(row[0] if row else "").strip() for row in rows[1:]]


def _is_strictly_sequential_integers(values: list[str]) -> bool:
    """Первая колонка — строго последовательные целые (2021, 2022, 2023 …)."""
    ints: list[int] = []
    for raw in values:
        num = _parse_number(raw)
        if num is None or abs(num - round(num)) > 1e-9:
            return False
        ints.append(int(round(num)))
    if len(ints) < 2:
        return False
    ordered = sorted(ints)
    return all(ordered[i] == ordered[i - 1] + 1 for i in range(1, len(ordered)))


def suggest_chart_type(
    rows: list[list[str]],
    *,
    context_text: str = "",
) -> ChartType:
    """
    Smart Chart: line для временных рядов, bar для категорий/номенклатуры.

    * LINE — ключевые слова времени в headers/1-й колонке или последовательные годы.
    * BAR — товары, категории, ФИО и прочие номинальные подписи.
    """
    rows = normalize_table_rows(rows)
    if len(rows) < 2:
        return ChartType.BAR

    headers_lower = [h.strip().lower() for h in rows[0]]
    first_col_lower = [v.strip().lower() for v in _first_column_values(rows)]
    context_lower = (context_text or "").strip().lower()

    if _contains_time_axis_keyword(*headers_lower, *first_col_lower, context_lower):
        return ChartType.LINE

    if _is_strictly_sequential_integers(_first_column_values(rows)):
        return ChartType.LINE

    return ChartType.BAR


def _resolve_chart_type(
    rows: list[list[str]],
    chart_type: ChartType,
    *,
    context_text: str = "",
) -> ChartType:
    if chart_type is ChartType.AUTO:
        return suggest_chart_type(rows, context_text=context_text)
    return chart_type


def render_chart_png_bytes(
    rows: list[list[str]],
    chart_type: ChartType = ChartType.AUTO,
    *,
    context_text: str = "",
) -> tuple[bytes | None, ChartType]:
    resolved = _resolve_chart_type(rows, chart_type, context_text=context_text)
    series = _extract_series(rows)
    if series is None:
        return None, resolved
    labels, values, header = series

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not installed — chart skipped")
        return None, resolved

    fig, ax = plt.subplots(figsize=(8, 4.8), dpi=120)
    fig.patch.set_facecolor("#f8fafc")
    color = "#2563eb"

    if resolved is ChartType.PIE:
        ax.set_facecolor("#ffffff")
        wedges, _texts, autotexts = ax.pie(
            values,
            labels=labels,
            autopct="%1.1f%%",
            startangle=90,
            colors=plt.cm.Blues([0.35 + 0.5 * i / max(len(values), 1) for i in range(len(values))]),
        )
        for t in autotexts:
            t.set_fontsize(8)
        ax.set_title(header, fontsize=12, fontweight="bold", pad=12)
    else:
        ax.set_facecolor("#ffffff")
        if resolved is ChartType.LINE:
            ax.plot(labels, values, marker="o", linewidth=2.2, color=color)
        else:
            ax.bar(labels, values, color=color, alpha=0.88)
        ax.set_title(header, fontsize=12, fontweight="bold", pad=12)
        ax.grid(True, axis="y", linestyle="--", alpha=0.35)
        ax.tick_params(axis="x", rotation=25 if len(labels) > 4 else 0, labelsize=8)

    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue(), resolved


def build_chart_png_bytes(
    rows: list[list[str]],
    *,
    context_text: str = "",
    chart_type: ChartType = ChartType.AUTO,
) -> bytes | None:
    png, _ = render_chart_png_bytes(rows, chart_type, context_text=context_text)
    return png


def build_xlsx_bytes(rows: list[list[str]]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    rows = normalize_table_rows(rows)
    wb = Workbook()
    ws = wb.active
    ws.title = "Отчёт"
    bold = Font(bold=True)
    for r_idx, row in enumerate(rows, start=1):
        for c_idx, cell in enumerate(row, start=1):
            ws.cell(row=r_idx, column=c_idx, value=cell)
            if r_idx == 1:
                ws.cell(row=r_idx, column=c_idx).font = bold
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def build_xlsx_bytes_from_table(headers: list[str], data_rows: list[list[str]]) -> bytes:
    """Сборка Excel из ``headers`` + ``rows`` (без regex/Markdown)."""
    rows = normalize_table_rows([headers, *data_rows])
    return build_xlsx_bytes(rows)


def _build_pack_from_rows(
    rows: list[list[str]],
    *,
    title: str | None = None,
    context_text: str = "",
    chart_type: ChartType = ChartType.AUTO,
) -> TableGeneratorPack | None:
    rows = normalize_table_rows(rows)
    if not rows:
        return None
    html_doc = markdown_table_to_html_document(rows)
    caption = markdown_table_to_telegram_caption(rows, title=title)
    if len(caption) > _CAPTION_MAX:
        caption = caption[: _CAPTION_MAX - 1] + "…"
    xlsx = build_xlsx_bytes(rows)
    chart_png, resolved = render_chart_png_bytes(rows, chart_type, context_text=context_text)
    return TableGeneratorPack(
        rows=rows,
        html_document=html_doc,
        telegram_caption_html=caption,
        xlsx_bytes=xlsx,
        chart_png_bytes=chart_png,
        chart_type=resolved,
    )


def build_table_generator_pack(
    raw_json: str,
    *,
    context_text: str = "",
    chart_type: ChartType = ChartType.AUTO,
) -> TableGeneratorPack | None:
    """Собирает отчёт из JSON-ответа OpenRouter (роль table_generator)."""
    payload = parse_table_json_response(raw_json)
    if payload is None:
        return None
    return _build_pack_from_rows(
        payload.to_rows_with_header(),
        title=payload.title,
        context_text=context_text,
        chart_type=chart_type,
    )


def build_table_generator_pack_from_payload(
    payload: TableJsonPayload,
    *,
    context_text: str = "",
    chart_type: ChartType = ChartType.AUTO,
) -> TableGeneratorPack | None:
    return _build_pack_from_rows(
        payload.to_rows_with_header(),
        title=payload.title,
        context_text=context_text,
        chart_type=chart_type,
    )


def build_table_generator_pack_from_rows(
    rows: list[list[str]],
    *,
    context_text: str = "",
    chart_type: ChartType = ChartType.AUTO,
    title: str | None = None,
) -> TableGeneratorPack | None:
    """Локальная сборка из строк (например, после чтения .xlsx)."""
    return _build_pack_from_rows(
        rows,
        title=title,
        context_text=context_text,
        chart_type=chart_type,
    )
