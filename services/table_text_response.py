"""Текстовый превью table_generator: «Один экран» без дублей таблиц от ИИ."""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.table_json import TableJsonPayload
from services.table_markdown import normalize_table_rows
from services.table_number_parse import parse_table_number, safe_float
from services.telegram_safe_text import _escape_telegram_html, repair_telegram_html

_SEPARATOR = "───────────────────"
_FINANCE_SEPARATOR = "────────────────────────"
_USN_RATE = 0.06
_ASCII_BORDER_RE = re.compile(r"^\+[-=+|]+\+$")
_PIPE_TABLE_RE = re.compile(r"^\s*\|.+\|\s*$")
_MATH_LINE_RE = re.compile(
    r"^\s*[\d\s.,₽$€]+\s*(?:\+|-|\*|/)\s*[\d\s.,₽$€]+(?:\s*(?:\+|-|\*|/)\s*[\d\s.,₽$€]+)*\s*=\s*[\d\s.,₽$€]+\s*$"
)
_MATH_INLINE_RE = re.compile(
    r"\b\d[\d\s.,]*\s*\+\s*\d[\d\s.,]*(?:\s*\+\s*\d[\d\s.,]*)*\s*=\s*\d[\d\s.,]*\b"
)
_MONEY_HINTS = ("руб", "₽", "выруч", "доход", "сумм", "перечисл", "прибыл", "amount", "revenue")
_QTY_HINTS = ("шт", "кол", "qty", "count", "единиц")
_TOTAL_LABEL_PREFIXES = ("итого", "всего", "total")


@dataclass(frozen=True)
class TableColumnMetrics:
    """Локальные метрики таблицы для Telegram и Excel."""

    headers: list[str]
    data_rows: list[list[str]]
    value_col: int
    label_col: int
    items: list[tuple[str, float]]
    total: float
    value_header: str
    label_header: str


def _is_totals_data_row(label: str) -> bool:
    low = (label or "").strip().lower()
    return any(low.startswith(prefix) for prefix in _TOTAL_LABEL_PREFIXES)


def _parse_number(raw: object) -> float | None:
    return parse_table_number(raw)


def fmt_money(value: float, *, suffix: str = "руб.") -> str:
    """60,000.50 руб. — коммерческий формат с разделителем тысяч."""
    if abs(value - round(value)) < 1e-9:
        body = f"{int(round(value)):,}"
    else:
        body = f"{value:,.2f}"
    return f"{body} {suffix}"


def _fmt_rub_in_code(value: float, *, decimals: int = 2) -> str:
    """Формат ``60,000.50`` для тега ``<code>`` (без суффикса)."""
    if decimals == 0:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


@dataclass(frozen=True)
class WbUnitTopRow:
    """Юнит-показатели одного товара (TOP-5 по выручке)."""

    label: str
    sale_price: float
    unit_logistics: float
    net_income: float


@dataclass(frozen=True)
class WbMarketplaceMetrics:
    """Локальные B2B-метрики WB/Ozon (без OpenRouter, 0 ₽)."""

    total_advertising_cost: float
    ad_load_pct: float
    sales_qty: float
    deliveries_qty: float
    returns_qty: float
    buyout_coef_pct: float
    unit_revenue: float
    top5_units: tuple[WbUnitTopRow, ...]
    insight_lines: tuple[str, ...]
    storage_cost: float = 0.0
    credit_deductions: float = 0.0
    logistics_cost: float = 0.0
    commission_cost: float = 0.0
    other_deductions: float = 0.0


_PROMO_AD_HINTS = ("продвижен", "реклам")
_AD_FALLBACK_HINTS = ("удержан",)
_SALES_QTY_HINTS = ("выкупили", "реализован", "продаж")
_DELIVERY_QTY_HINTS = ("доставк", "к клиенту")
_RETURN_QTY_HINTS = ("возврат",)
_ORDERED_QTY_HINTS = ("заказано", "заказ")
_QTY_UNIT_HINTS = ("шт", "кол-во", "количество", "единиц")
_REVENUE_HINTS = ("перечислению", "выруч", "заработок")
_LABEL_HINTS = ("предмет", "артикул", "наименование", "номенклатур", "бренд")
_PRICE_HINTS = ("цена", "реализац", "рознич")
_COMMISSION_HINTS = ("вознагражден", "комисс")
_LOGISTICS_HINTS = ("логистик", "доставк", "хранен")
_RETURN_LOGISTICS_HINTS = ("возврат", "обратн")
_RETURN_ID_SKIP = (
    "srid",
    "rrid",
    "rrd",
    "id ",
    " id",
    "номер",
    "код возврата",
    "документ",
    "транзак",
)


def _is_return_id_column(header: str) -> bool:
    low = (header or "").lower()
    if any(q in low for q in _QTY_UNIT_HINTS):
        return False
    return any(s in low for s in _RETURN_ID_SKIP)


def _match_column_index(headers: list[str], hints: tuple[str, ...], *, require_qty: bool = False) -> int | None:
    for idx, header in enumerate(headers):
        low = (header or "").lower()
        if not any(h in low for h in hints):
            continue
        if require_qty and not any(q in low for q in _QTY_UNIT_HINTS):
            continue
        if any(h in low for h in _RETURN_QTY_HINTS) and _is_return_id_column(header):
            continue
        return idx
    return None


def _sum_numeric_column(matrix: list[list[str]], col_idx: int) -> float:
    total = 0.0
    for row in matrix[1:]:
        if col_idx >= len(row):
            continue
        total += safe_float(row[col_idx])
    return total


def _sum_promo_advertising_columns(matrix: list[list[str]], headers: list[str]) -> float:
    """Сумма удержаний за продвижение / рекламу по всем подходящим колонкам."""
    from services.wb_transaction_parse import aggregate_wb_transactions, is_wb_transaction_report

    if is_wb_transaction_report(headers):
        tx = aggregate_wb_transactions(matrix)
        if tx is not None:
            return tx.total_advertising_cost

    promo_cols: list[int] = []
    for idx, header in enumerate(headers):
        low = (header or "").lower()
        if any(x in low for x in ("кредит", "хранен")):
            continue
        if any(h in low for h in _PROMO_AD_HINTS):
            promo_cols.append(idx)
        elif any(h in low for h in _AD_FALLBACK_HINTS) and "продвижен" in low:
            promo_cols.append(idx)
    if not promo_cols:
        promo_cols = [
            idx
            for idx, header in enumerate(headers)
            if any(h in (header or "").lower() for h in _PROMO_AD_HINTS + _AD_FALLBACK_HINTS)
            and "кредит" not in (header or "").lower()
            and "хранен" not in (header or "").lower()
        ]
    total = 0.0
    for col_idx in promo_cols:
        total += abs(_sum_numeric_column(matrix, col_idx))
    return total


def _pick_wb_label_column(headers: list[str]) -> int:
    col = _match_column_index(headers, _LABEL_HINTS)
    return col if col is not None else 0


def _aggregate_top5_units(
    matrix: list[list[str]],
    headers: list[str],
    *,
    platform: str | None = None,
) -> tuple[WbUnitTopRow, ...]:
    """TOP-5 товаров по выручке: цена продажи, юнит-логистика, чистый доход на 1 шт."""
    from services.marketplace_platform import get_marketplace_profile

    if len(matrix) < 2:
        return ()

    profile = get_marketplace_profile(platform)
    label_col = _pick_wb_label_column(headers)
    rev_col = _match_column_index(headers, profile.revenue_hints)
    qty_col = _match_column_index(headers, profile.sales_hints, require_qty=True)
    if qty_col is None:
        qty_col = _match_column_index(headers, profile.sales_hints)
    price_col = _match_column_index(headers, _PRICE_HINTS)
    comm_col = _match_column_index(headers, profile.commission_hints)
    log_col = _match_column_index(headers, profile.logistics_hints)
    ret_log_col = _match_column_index(headers, _RETURN_LOGISTICS_HINTS)
    extra_cols = [
        idx
        for idx, h in enumerate(headers)
        if any(x in (h or "").lower() for x in profile.extra_deduction_hints)
    ]

    @dataclass
    class _Agg:
        revenue: float = 0.0
        qty: float = 0.0
        commission: float = 0.0
        logistics: float = 0.0
        extra: float = 0.0
        price_sum: float = 0.0
        price_count: int = 0

    buckets: dict[str, _Agg] = {}
    for row in matrix[1:]:
        label = (row[label_col] if label_col < len(row) else "").strip() or "—"
        if label.lower().startswith(("итого", "всего", "total")):
            continue
        agg = buckets.setdefault(label, _Agg())
        qty = safe_float(row[qty_col]) if qty_col is not None and qty_col < len(row) else 0.0
        rev = safe_float(row[rev_col]) if rev_col is not None and rev_col < len(row) else 0.0
        comm = safe_float(row[comm_col]) if comm_col is not None and comm_col < len(row) else 0.0
        log_d = safe_float(row[log_col]) if log_col is not None and log_col < len(row) else 0.0
        log_r = safe_float(row[ret_log_col]) if ret_log_col is not None and ret_log_col < len(row) else 0.0
        if price_col is not None and price_col < len(row):
            p = safe_float(row[price_col])
            if p > 0:
                agg.price_sum += p
                agg.price_count += 1
        agg.revenue += rev
        agg.qty += qty
        agg.commission += abs(comm)
        agg.logistics += abs(log_d) + abs(log_r)
        for ec in extra_cols:
            if ec < len(row):
                agg.extra += abs(safe_float(row[ec]))

    ranked = sorted(buckets.items(), key=lambda item: item[1].revenue, reverse=True)[:5]
    out: list[WbUnitTopRow] = []
    for label, agg in ranked:
        if agg.revenue <= 0 and agg.qty <= 0:
            continue
        sale_price = (
            agg.price_sum / agg.price_count
            if agg.price_count > 0
            else (agg.revenue / agg.qty if agg.qty > 0 else 0.0)
        )
        unit_logistics = agg.logistics / agg.qty if agg.qty > 0 else 0.0
        unit_commission = agg.commission / agg.qty if agg.qty > 0 else 0.0
        unit_extra = agg.extra / agg.qty if agg.qty > 0 else 0.0
        net_income = sale_price - unit_commission - unit_logistics - unit_extra
        out.append(
            WbUnitTopRow(
                label=label[:48],
                sale_price=sale_price,
                unit_logistics=unit_logistics,
                net_income=net_income,
            )
        )
    return tuple(out)


def _build_wb_insight_lines(
    *,
    ad_load_pct: float,
    buyout_coef_pct: float,
    unit_revenue: float,
    total_advertising_cost: float,
    top5_units: tuple[WbUnitTopRow, ...],
) -> tuple[str, ...]:
    lines: list[str] = []
    if total_advertising_cost > 0:
        if ad_load_pct > 20:
            lines.append(
                "• <i>Рекламная нагрузка:</i> "
                f"<code>{ad_load_pct:.1f}%</code> ({_fmt_rub_in_code(total_advertising_cost)} руб.) — "
                "критическая зона: срежьте неэффективные кампании."
            )
        elif ad_load_pct > 10:
            lines.append(
                "• <i>Рекламная нагрузка:</i> "
                f"<code>{ad_load_pct:.1f}%</code> — умеренная, контролируйте ДРР."
            )
        else:
            lines.append(
                "• <i>Рекламная нагрузка:</i> "
                f"<code>{ad_load_pct:.1f}%</code> — низкая, есть запас для масштабирования."
            )
    if buyout_coef_pct > 0:
        if buyout_coef_pct < 40:
            lines.append(
                "• <i>Коэффициент выкупа:</i> "
                f"<code>{buyout_coef_pct:.1f}%</code> — низкий, риск возвратов и логистических потерь."
            )
        elif buyout_coef_pct < 65:
            lines.append(
                "• <i>Коэффициент выкупа:</i> "
                f"<code>{buyout_coef_pct:.1f}%</code> — средний, оптимизируйте карточки и цены."
            )
        else:
            lines.append(
                "• <i>Коэффициент выкупа:</i> "
                f"<code>{buyout_coef_pct:.1f}%</code> — хороший показатель для маркетплейса."
            )
    if top5_units:
        best = max(top5_units, key=lambda u: u.net_income)
        worst = min(top5_units, key=lambda u: u.net_income)
        lines.append(
            "• <i>Юнит TOP-5:</i> лидер «"
            f"{_escape_telegram_html(best.label)}» — чистый доход "
            f"<code>{_fmt_rub_in_code(best.net_income)}</code>/шт."
        )
        if worst.net_income < 0:
            lines.append(
                "• <i>Критическая зона:</i> «"
                f"{_escape_telegram_html(worst.label)}» — убыточная юнит-экономика "
                f"(<code>{_fmt_rub_in_code(worst.net_income)}</code>/шт)."
            )
    elif unit_revenue > 0:
        lines.append(
            "• <i>Средняя юнит-выручка:</i> "
            f"<code>{_fmt_rub_in_code(unit_revenue)} руб.</code> на 1 проданную единицу."
        )
    if not lines:
        lines.append(
            "• <i>Совет:</i> Налог УСН 6% можно уменьшить на сумму фиксированных взносов ИП "
            "внутри отчётного квартала."
        )
    return tuple(lines)


def compute_wb_marketplace_metrics(
    matrix: list[list[str]],
    *,
    revenue_total: float,
    platform: str | None = None,
) -> WbMarketplaceMetrics | None:
    """Реклама, юнит TOP-5 и коэффициент выкупа — только Python (cfo-v10 hybrid ETL)."""
    from services.wb_report_parser import parse_wb_report

    if not matrix or len(matrix) < 2 or revenue_total <= 0:
        return None

    headers = matrix[0]
    model = parse_wb_report(matrix, platform=platform)

    if model is not None:
        total_ad = model.ad_spend
        storage_cost = model.storage_cost
        credit_deductions = model.credit_deductions
        logistics_cost = model.logistics_cost
        commission_cost = model.commission_cost
        other_deductions = model.other_deductions
        sales_qty = model.sales_qty
        deliveries_qty = model.deliveries_qty
        returns_qty = model.returns_qty
        buyout_coef_pct = model.buyout_coef_pct
    else:
        from services.marketplace_platform import get_marketplace_profile

        profile = get_marketplace_profile(platform)
        total_ad = _sum_promo_advertising_columns(matrix, headers)
        storage_cost = 0.0
        credit_deductions = 0.0
        logistics_cost = 0.0
        commission_cost = 0.0
        other_deductions = 0.0
        for idx, header in enumerate(headers):
            low = (header or "").lower()
            if any(h in low for h in profile.extra_deduction_hints):
                if any(h in low for h in profile.ad_hints) or any(
                    x in low for x in ("буст", "boost", "реклам", "продвижен", "трафарет")
                ):
                    if "кредит" not in low and "хранен" not in low:
                        total_ad += _sum_numeric_column(matrix, idx)

        sales_col = _match_column_index(headers, profile.sales_hints, require_qty=True)
        if sales_col is None:
            sales_col = _match_column_index(headers, profile.sales_hints)
        del_col = _match_column_index(headers, profile.delivery_hints, require_qty=True)
        if del_col is None:
            del_col = _match_column_index(headers, profile.delivery_hints)
        ret_col = _match_column_index(headers, profile.return_hints, require_qty=True)
        if ret_col is None:
            ret_col = _match_column_index(headers, profile.return_hints)
        ordered_col = _match_column_index(headers, _ORDERED_QTY_HINTS, require_qty=True)
        if ordered_col is None:
            ordered_col = _match_column_index(headers, _ORDERED_QTY_HINTS)

        sales_qty = _sum_numeric_column(matrix, sales_col) if sales_col is not None else 0.0
        deliveries_qty = _sum_numeric_column(matrix, del_col) if del_col is not None else 0.0
        raw_returns = _sum_numeric_column(matrix, ret_col) if ret_col is not None else 0.0
        returns_qty = raw_returns
        if returns_qty > 0:
            if deliveries_qty > 0:
                returns_qty = min(returns_qty, deliveries_qty)
            if sales_qty > 0:
                returns_qty = min(returns_qty, sales_qty * 2.0)
        ordered_qty = _sum_numeric_column(matrix, ordered_col) if ordered_col is not None else 0.0

        from services.file_processor import compute_buyout_coef_pct

        buyout_coef_pct = compute_buyout_coef_pct(sales_qty, returns_qty)
        if buyout_coef_pct <= 0 and ordered_qty > 0 and sales_qty > 0:
            buyout_coef_pct = sales_qty / ordered_qty * 100.0

    ad_load_pct = model.drr_pct if model is not None else (
        (total_ad / revenue_total * 100.0) if revenue_total > 0 and total_ad > 0 else 0.0
    )
    unit_revenue = (revenue_total / sales_qty) if sales_qty > 0 else 0.0
    top5_units = _aggregate_top5_units(matrix, headers, platform=platform)

    insight_lines = _build_wb_insight_lines(
        ad_load_pct=ad_load_pct,
        buyout_coef_pct=buyout_coef_pct,
        unit_revenue=unit_revenue,
        total_advertising_cost=total_ad,
        top5_units=top5_units,
    )
    return WbMarketplaceMetrics(
        total_advertising_cost=total_ad,
        ad_load_pct=ad_load_pct,
        sales_qty=sales_qty,
        deliveries_qty=deliveries_qty,
        returns_qty=returns_qty,
        buyout_coef_pct=buyout_coef_pct,
        unit_revenue=unit_revenue,
        top5_units=top5_units,
        insight_lines=insight_lines,
        storage_cost=storage_cost,
        credit_deductions=credit_deductions,
        logistics_cost=logistics_cost,
        commission_cost=commission_cost,
        other_deductions=other_deductions,
    )


def build_wb_finance_express_html(
    calculated_total: float,
    *,
    wb_metrics: WbMarketplaceMetrics | None = None,
    matrix_rows: list[list[str]] | None = None,
    platform: str | None = None,
) -> str:
    """
    Локальный fallback для под-режима ``wb_ozon_finance`` (если ИИ недоступен).

    Расчёт УСН 6%, чистой прибыли, скоринга, FOMO и CTA — только на сервере.
    """
    if calculated_total <= 0:
        return ""

    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    prompt_metrics = compute_wb_finance_prompt_metrics(
        calculated_total, wb_metrics, matrix_rows=matrix_rows, platform=platform
    )
    if prompt_metrics is None:
        return ""
    return build_wb_finance_express_html_local(prompt_metrics, wb_metrics)

def fmt_count(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.1f}"


def strip_ascii_tables(text: str) -> str:
    """Удаляет ASCII/Markdown-таблицы из текста модели."""
    if not text:
        return ""
    kept: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.strip()
        if _ASCII_BORDER_RE.match(stripped):
            continue
        if _PIPE_TABLE_RE.match(line):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def strip_math_formulas(text: str) -> str:
    """Убирает строки и фрагменты «60000 + 55000 = 130000»."""
    if not text:
        return ""
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        cleaned = _MATH_INLINE_RE.sub("", line)
        if _MATH_LINE_RE.match(cleaned.strip()):
            continue
        cleaned = cleaned.strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip()


def extract_table_ai_insights(raw_answer: str) -> str:
    """Текст модели вне JSON-блока — только для аналитического заключения."""
    text = (raw_answer or "").strip()
    if not text:
        return ""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        prose = f"{text[:start]}\n{text[end + 1 :]}".strip()
    else:
        prose = text
    prose = strip_ascii_tables(prose)
    prose = strip_math_formulas(prose)
    prose = re.sub(r"\n{3,}", "\n\n", prose)
    return prose.strip()


def _pick_value_column(headers: list[str], rows: list[list[str]]) -> int:
    from services.table_xlsx_preprocess import find_revenue_column_index

    revenue_col = find_revenue_column_index(headers)
    if revenue_col is not None:
        return revenue_col
    lowered = [h.lower() for h in headers]
    for idx, header in enumerate(lowered):
        if any(h in header for h in _MONEY_HINTS):
            return idx
    for idx, header in enumerate(lowered):
        if any(h in header for h in _QTY_HINTS):
            continue
        nums = [_parse_number(row[idx] if idx < len(row) else "") for row in rows]
        valid = [n for n in nums if n is not None]
        if len(valid) >= max(1, len(rows) // 2):
            return idx
    for idx in range(len(headers)):
        nums = [_parse_number(row[idx] if idx < len(row) else "") for row in rows]
        if sum(1 for n in nums if n is not None) >= max(1, len(rows) // 2):
            return idx
    return min(1, len(headers) - 1) if len(headers) > 1 else 0


def _pick_label_column(headers: list[str], value_col: int) -> int:
    for idx, header in enumerate(headers):
        if idx == value_col:
            continue
        if (header or "").strip():
            return idx
    return 0


def compute_table_column_metrics(rows: list[list[str]]) -> TableColumnMetrics | None:
    """Считает итоги по основной числовой колонке (без строк «Итого»)."""
    matrix = normalize_table_rows(rows)
    if len(matrix) < 2:
        return None

    headers = matrix[0]
    provisional_value_col = _pick_value_column(headers, matrix[1:])
    provisional_label_col = _pick_label_column(headers, provisional_value_col)

    data_rows: list[list[str]] = []
    for row in matrix[1:]:
        label = (row[provisional_label_col] if provisional_label_col < len(row) else "").strip()
        if _is_totals_data_row(label):
            continue
        data_rows.append(row)

    if not data_rows:
        return None

    value_col = _pick_value_column(headers, data_rows)
    label_col = _pick_label_column(headers, value_col)
    value_header = headers[value_col] if value_col < len(headers) else "Значение"
    label_header = headers[label_col] if label_col < len(headers) else "Период"

    items: list[tuple[str, float]] = []
    for row in data_rows:
        label = (row[label_col] if label_col < len(row) else "").strip() or "—"
        num = _parse_number(row[value_col] if value_col < len(row) else "")
        if num is None:
            continue
        items.append((label, num))

    if not items:
        return None

    total = sum(safe_float(value) for _, value in items)
    return TableColumnMetrics(
        headers=headers,
        data_rows=data_rows,
        value_col=value_col,
        label_col=label_col,
        items=items,
        total=total,
        value_header=value_header,
        label_header=label_header,
    )


def _metrics_from_payload(payload: TableJsonPayload) -> tuple[list[tuple[str, float]], float, str, str]:
    metrics = compute_table_column_metrics(payload.to_rows_with_header())
    if metrics is None:
        return [], 0.0, "Показатель", "Значение"
    return metrics.items, metrics.total, metrics.label_header, metrics.value_header


def _is_money_column(header: str) -> bool:
    low = (header or "").lower()
    return any(h in low for h in _MONEY_HINTS)


def build_table_one_screen_html(
    payload: TableJsonPayload,
    *,
    ai_insights: str = "",
    total_override: float | None = None,
    table_subrole: str | None = None,
) -> str:
    """
    Коммерческий шаблон «Один экран»: ИТОГО (локально) + emoji-список + вывод ИИ.

    Для ``wb_ozon_finance`` — премиальный финансовый экспресс-анализ без OpenRouter.
    """
    from services.table_subrole_types import normalize_table_subrole

    items, total, label_header, value_header = _metrics_from_payload(payload)
    if total_override is not None and total_override > 0:
        total = total_override
    if not items:
        return ""

    if normalize_table_subrole(table_subrole) == "wb_ozon_finance" and total > 0:
        wb_metrics = compute_wb_marketplace_metrics(
            payload.to_rows_with_header(),
            revenue_total=total,
        )
        return build_wb_finance_express_html(
            total,
            wb_metrics=wb_metrics,
            matrix_rows=payload.to_rows_with_header(),
        )

    count = len(items)
    average = total / count if count else 0.0
    money_mode = _is_money_column(value_header)
    title = _escape_telegram_html((payload.title or "Отчёт").strip())

    lines: list[str] = [
        f"📊 <b>{title}</b>",
        _SEPARATOR,
    ]
    if money_mode:
        lines.append(f"💰 <b>ИТОГО:</b> {fmt_money(total)}")
    else:
        lines.append(f"💰 <b>ИТОГО:</b> <code>{fmt_count(total)}</code>")
    lines.append(f"📦 <b>Периодов / строк:</b> {count}")
    if money_mode:
        lines.append(f"📈 <b>Среднее:</b> {fmt_money(average)}")
    else:
        lines.append(f"📈 <b>Среднее:</b> <code>{fmt_count(average)}</code>")
    lines.extend(
        [
            _SEPARATOR,
            f"🔝 <b>Показатели по {label_header.lower()}:</b>",
            "",
        ]
    )

    for idx, (label, value) in enumerate(items, start=1):
        safe_label = _escape_telegram_html(label[:48] + ("…" if len(label) > 48 else ""))
        if money_mode:
            value_text = fmt_money(value)
        else:
            value_text = f"{fmt_count(value)}"
        lines.append(f"{idx}. 📅 <b>{safe_label}</b> — <code>{value_text}</code>")

    insights = strip_math_formulas(strip_ascii_tables(ai_insights))
    insights = re.sub(r"\s+", " ", insights).strip()
    if insights:
        safe_insights = _escape_telegram_html(insights)
        lines.extend(
            [
                "",
                _SEPARATOR,
                "🧠 <b>Аналитическое заключение:</b>",
                f"<i>{safe_insights}</i>",
            ]
        )

    return repair_telegram_html("\n".join(lines))
