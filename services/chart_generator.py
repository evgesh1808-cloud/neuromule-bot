"""Горизонтальные графики Matplotlib (CFO v11.1) — синхрон с rrc_revenue финотчёта."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO
from typing import Any

try:
    import matplotlib

    matplotlib.use("Agg")
except ImportError:
    matplotlib = None  # type: ignore[assignment,misc]

_CHART_COLOR = "#2ecc71"
_CHART_BG = "#f8fafc"
_TOP_N = 7
_OTHERS_LABEL = "Другие"


@dataclass(frozen=True)
class HorizontalBarSeries:
    """Подписи и значения для горизонтального barh (выручка РРЦ, руб.)."""

    labels: list[str]
    values: list[float]
    value_axis_label: str = "Выручка РРЦ, руб."
    chart_title: str = "Топ товаров по выручке (РРЦ)"


def format_rub_thousands(value: float) -> str:
    """Пробелы-разделители тысяч для подписей оси и столбцов."""
    rounded = round(float(value))
    return f"{rounded:,}".replace(",", " ")


def _truncate_label(text: str, *, max_len: int = 28) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if len(clean) <= max_len:
        return clean or "—"
    return clean[: max_len - 1] + "…"


def build_rrc_chart_series_from_sku_data(
    sku_data: dict[str, dict[str, Any]],
) -> HorizontalBarSeries | None:
    """Серия TOP-N из ``sku_data`` (ключи CFO: ``rrc_revenue``)."""
    if not sku_data:
        return None

    grouped: dict[str, float] = defaultdict(float)
    for sku, stats in sku_data.items():
        label = _truncate_label(str(sku))
        revenue = round(float(stats.get("rrc_revenue", 0.0) or 0.0), 2)
        if revenue <= 0:
            continue
        grouped[label] += revenue

    if not grouped:
        return None

    sorted_items = sorted(grouped.items(), key=lambda item: item[1], reverse=True)
    top = sorted_items[:_TOP_N]
    rest_sum = round(sum(v for _, v in sorted_items[_TOP_N:]), 2)

    labels = [name for name, _ in top]
    values = [round(val, 2) for _, val in top]
    if rest_sum > 0:
        labels.append(_OTHERS_LABEL)
        values.append(rest_sum)

    return HorizontalBarSeries(labels=labels, values=values)


def build_rrc_chart_series_from_final_metrics(
    metrics: dict[str, Any],
) -> HorizontalBarSeries | None:
    """Серия из ``final_metrics_json`` / ``build_cfo_metrics_dict``."""
    sku_data = metrics.get("sku_data")
    if isinstance(sku_data, dict) and sku_data:
        series = build_rrc_chart_series_from_sku_data(sku_data)
        if series is not None:
            return series

    catalog = metrics.get("sku_catalog")
    if not isinstance(catalog, list):
        return None

    grouped: dict[str, float] = defaultdict(float)
    for item in catalog:
        if not isinstance(item, dict):
            continue
        label = _truncate_label(
            str(item.get("label") or item.get("name") or item.get("article_id") or "—")
        )
        revenue = round(
            float(
                item.get("rrc_revenue")
                if item.get("rrc_revenue") is not None
                else item.get("revenue_rub", 0.0)
            ),
            2,
        )
        if revenue <= 0:
            continue
        grouped[label] += revenue

    if not grouped:
        return None

    sorted_items = sorted(grouped.items(), key=lambda x: x[1], reverse=True)
    top = sorted_items[:_TOP_N]
    rest_sum = round(sum(v for _, v in sorted_items[_TOP_N:]), 2)
    labels = [n for n, _ in top]
    values = [round(v, 2) for _, v in top]
    if rest_sum > 0:
        labels.append(_OTHERS_LABEL)
        values.append(rest_sum)
    return HorizontalBarSeries(labels=labels, values=values)


def build_rrc_chart_series_from_rows(
    rows: list[list[str]],
    *,
    platform: str = "wildberries",
    tax_type: str = "USN",
    tax_rate: float = 6.0,
) -> HorizontalBarSeries | None:
    """CFO Engine v11.1: те же ``rrc_revenue``, что в текстовом финотчёте."""
    if not rows or len(rows) < 2:
        return None

    from services.file_processor import build_cfo_metrics_dict_from_rows

    metrics = build_cfo_metrics_dict_from_rows(rows, platform, tax_type, tax_rate)
    if metrics.get("error"):
        return None
    return build_rrc_chart_series_from_final_metrics(metrics)


def render_horizontal_bar_chart_png(series: HorizontalBarSeries) -> bytes | None:
    """Современный горизонтальный barh: без верхней/правой рамки, xlim с запасом под подписи."""
    if matplotlib is None or not series.values:
        return None
    try:
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter
    except ImportError:
        return None

    labels = series.labels
    values = series.values
    max_revenue = max(values) if values else 0.0
    if max_revenue <= 0:
        return None

    fig_h = max(4.2, 0.45 * len(labels) + 1.8)
    fig, ax = plt.subplots(figsize=(8.5, fig_h), dpi=120)
    fig.patch.set_facecolor(_CHART_BG)
    ax.set_facecolor("#ffffff")

    y_pos = range(len(labels))
    bars = ax.barh(
        list(y_pos),
        values,
        color=_CHART_COLOR,
        alpha=0.9,
        edgecolor="#27ae60",
        linewidth=0.6,
        height=0.62,
    )
    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel(series.value_axis_label, fontsize=10, labelpad=8)
    ax.set_ylabel("Товары", fontsize=10, labelpad=8)
    ax.set_title(series.chart_title, fontsize=13, fontweight="bold", pad=12)
    ax.grid(True, axis="x", linestyle="--", alpha=0.35)
    ax.tick_params(axis="x", labelsize=8)

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_xlim(0, max_revenue * 1.12)
    ax.xaxis.set_major_formatter(
        FuncFormatter(lambda x, _pos: format_rub_thousands(x))
    )

    label_offset = max_revenue * 0.01
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_width() + label_offset,
            bar.get_y() + bar.get_height() / 2,
            format_rub_thousands(val),
            va="center",
            ha="left",
            fontsize=8,
            color="#1e293b",
        )

    plt.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
