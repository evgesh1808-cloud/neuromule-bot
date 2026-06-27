"""Smart Chart для отчётов Wildberries: группировка, TOP-7, matplotlib."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO

from services.table_markdown import normalize_table_rows
from services.table_number_parse import parse_table_number

try:
    import matplotlib

    matplotlib.use("Agg")
except ImportError:
    matplotlib = None  # type: ignore[assignment,misc]

_WB_TOP_N = 7
_OTHERS_LABEL = "Другие"
_CHART_COLOR = "#2ecc71"
_CHART_BG = "#f8fafc"

_LABEL_COLUMN_RULES: tuple[tuple[str, ...], ...] = (
    ("предмет",),
    ("артикул", "продавца"),
    ("артикул",),
    ("наименование",),
)

_VALUE_COLUMN_RULES: tuple[tuple[str, ...], ...] = (
    ("перечислению", "товар"),
    ("перечислению", "руб"),
    ("сумма", "заказов", "комиссия"),
    ("сумма", "заказов"),
    ("выкупили", "шт"),
    ("выкупили",),
    ("заказано", "шт"),
)

_UNIT_RUB_HINTS = ("руб", "перечислению", "сумма", "выручка", "комиссия")


@dataclass(frozen=True)
class WbSalesSeries:
    """Сгруппированные данные для столбчатого графика WB."""

    labels: list[str]
    values: list[float]
    value_axis_label: str
    chart_title: str = "Топ товаров по выручке"
    is_revenue: bool = True


def _parse_number(raw: object) -> float | None:
    return parse_table_number(raw)


def _match_column_index(headers: list[str], rules: tuple[tuple[str, ...], ...]) -> int | None:
    lowered = [h.lower() for h in headers]
    for patterns in rules:
        for idx, header in enumerate(lowered):
            if not header:
                continue
            if all(part in header for part in patterns):
                return idx
    return None


def _value_axis_label(header: str) -> tuple[str, bool]:
    low = (header or "").lower()
    if any(h in low for h in _UNIT_RUB_HINTS):
        return "Выручка, руб.", True
    if "шт" in low or "кол" in low:
        return header.strip() or "Количество, шт.", False
    return header.strip() or "Значение", False


def _truncate_label(text: str, *, max_len: int = 28) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if len(clean) <= max_len:
        return clean or "—"
    return clean[: max_len - 1] + "…"


def extract_wb_sales_series(rows: list[list[str]]) -> WbSalesSeries | None:
    """
    Находит колонки WB, группирует по «Предмет»/«Артикул», оставляет TOP-7 + «Другие».
    """
    matrix = normalize_table_rows(rows)
    if len(matrix) < 2:
        return None

    headers = matrix[0]
    label_col = _match_column_index(headers, _LABEL_COLUMN_RULES)
    value_col = _match_column_index(headers, _VALUE_COLUMN_RULES)
    if label_col is None or value_col is None:
        return None

    grouped: dict[str, float] = defaultdict(float)
    for row in matrix[1:]:
        label = (row[label_col] if label_col < len(row) else "").strip()
        if not label or label.lower().startswith("итого"):
            continue
        num = _parse_number(row[value_col] if value_col < len(row) else "")
        if num is None:
            continue
        grouped[label] += num

    if len(grouped) < 1:
        return None

    sorted_items = sorted(grouped.items(), key=lambda item: item[1], reverse=True)
    top = sorted_items[:_WB_TOP_N]
    rest_sum = sum(v for _, v in sorted_items[_WB_TOP_N:])

    labels = [_truncate_label(name) for name, _ in top]
    values = [val for _, val in top]
    if rest_sum > 0:
        labels.append(_OTHERS_LABEL)
        values.append(rest_sum)

    if len(values) < 1:
        return None

    value_header = headers[value_col] if value_col < len(headers) else "Значение"
    axis_label, is_revenue = _value_axis_label(value_header)
    title = "Топ товаров по выручке" if is_revenue else "Топ товаров по объёму"

    return WbSalesSeries(
        labels=labels,
        values=values,
        value_axis_label=axis_label,
        chart_title=title,
        is_revenue=is_revenue,
    )


def _normalize_wb_chart_type(chart_type: str) -> str:
    key = (chart_type or "barh").strip().lower()
    if key in ("bar", "barh", "histogram"):
        return "barh"
    if key in ("line", "plot"):
        return "line"
    if key in ("pie", "circle"):
        return "pie"
    return key


def render_wb_sales_chart_png(
    series: WbSalesSeries,
    chart_type: str = "barh",
) -> bytes | None:
    """Отрисовка WB-графика: ``barh`` | ``line`` | ``pie`` (Agg, без GUI)."""
    if matplotlib is None:
        return None

    kind = _normalize_wb_chart_type(chart_type)
    labels = series.labels
    values = series.values

    if kind == "barh":
        from services.chart_generator import HorizontalBarSeries, render_horizontal_bar_chart_png

        return render_horizontal_bar_chart_png(
            HorizontalBarSeries(
                labels=labels,
                values=values,
                value_axis_label=series.value_axis_label,
                chart_title=series.chart_title,
            )
        )

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    if len(values) < 2:
        return None

    fig, ax = plt.subplots(figsize=(8.5, 4.8), dpi=120)
    fig.patch.set_facecolor(_CHART_BG)
    ax.set_facecolor("#ffffff")

    if kind == "pie":
        colors = plt.cm.Greens([0.35 + 0.55 * i / max(len(values), 1) for i in range(len(values))])
        _wedges, _texts, autotexts = ax.pie(
            values,
            labels=labels,
            autopct="%1.1f%%",
            startangle=90,
            colors=colors,
            textprops={"fontsize": 8},
        )
        for t in autotexts:
            t.set_fontsize(8)
        ax.set_title(series.chart_title, fontsize=13, fontweight="bold", pad=12)
    elif kind == "line":
        x_pos = list(range(len(labels)))
        ax.plot(
            x_pos,
            values,
            marker="o",
            linewidth=2.2,
            color=_CHART_COLOR,
            markersize=7,
        )
        ax.set_xticks(x_pos)
        ax.set_xticklabels(labels, rotation=25 if len(labels) > 4 else 0, fontsize=8)
        ax.set_ylabel(series.value_axis_label, fontsize=10, labelpad=8)
        ax.set_xlabel("Товары", fontsize=10, labelpad=8)
        ax.set_title(series.chart_title, fontsize=13, fontweight="bold", pad=12)
        ax.grid(True, axis="y", linestyle="--", alpha=0.35)
        ax.tick_params(axis="x", labelsize=8)

    plt.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def render_wb_chart_from_rows(
    rows: list[list[str]],
    chart_type: str = "barh",
    *,
    final_metrics: dict | None = None,
    tax_type: str = "USN",
    tax_rate: float = 6.0,
) -> bytes | None:
    """Извлекает WB-серию и рисует PNG; barh — по ``rrc_revenue`` CFO v11.1."""
    kind = _normalize_wb_chart_type(chart_type)
    if kind == "barh":
        from services.chart_generator import (
            build_rrc_chart_series_from_final_metrics,
            build_rrc_chart_series_from_rows,
            render_horizontal_bar_chart_png,
        )

        rrc_series = None
        if final_metrics:
            rrc_series = build_rrc_chart_series_from_final_metrics(final_metrics)
        if rrc_series is None:
            rrc_series = build_rrc_chart_series_from_rows(
                rows,
                tax_type=tax_type,
                tax_rate=tax_rate,
            )
        if rrc_series is not None:
            png = render_horizontal_bar_chart_png(rrc_series)
            if png is not None:
                return png

    series = extract_wb_sales_series(rows)
    if series is None:
        return None
    return render_wb_sales_chart_png(series, chart_type=chart_type)


def try_render_wb_chart_png(
    rows: list[list[str]],
    *,
    final_metrics: dict | None = None,
    tax_type: str = "USN",
    tax_rate: float = 6.0,
) -> bytes | None:
    """WB fast-path: barh по ``rrc_revenue`` CFO v11.1."""
    return render_wb_chart_from_rows(
        rows,
        chart_type="barh",
        final_metrics=final_metrics,
        tax_type=tax_type,
        tax_rate=tax_rate,
    )
