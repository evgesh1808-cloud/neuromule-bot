"""
Единая точка парсинга отчётов Wildberries (детализация транзакций и SKU-матрица).

Использование::

    model = parse_wb_report(matrix, platform="wildberries")
    if model:
        revenue = model.revenue
        drr = model.drr_pct
        for sku in model.sku_metrics():
            ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from services.table_number_parse import safe_float
from services.wb_transaction_parse import (
    WbSkuTxBucket,
    aggregate_wb_transactions,
    classify_wb_transaction_row,
    is_valid_wb_sku,
    is_wb_transaction_report,
    resolve_wb_tx_columns,
)
from services.wb_transaction_parse import _row_text_blob as tx_row_blob

WbReportKind = Literal["transaction", "matrix", "unknown"]

_USN_RATE = 0.06
_MATRIX_COST_HINTS = ("себестоимость", "себестоим", "себес", "закупка", "закуп", "cost")
_TOTAL_PREFIXES = ("итого", "всего", "total")
_PROMO_AD_HINTS = ("продвижен", "реклам")
_AD_FALLBACK_HINTS = ("удержан",)


@dataclass
class WbSkuMetrics:
    """Метрики одного SKU после нормализации отчёта."""

    name: str
    article_id: str
    revenue: float = 0.0
    sales_qty: float = 0.0
    deliveries_qty: float = 0.0
    returns_qty: float = 0.0
    logistics: float = 0.0
    commission: float = 0.0
    ad_cost: float = 0.0
    extra_cost: float = 0.0
    cost_rub: float = 0.0
    stock_qty: float = 0.0
    return_logistics_rub: float = 0.0

    @property
    def net_profit(self) -> float:
        return (
            self.revenue
            - self.commission
            - self.logistics
            - self.ad_cost
            - self.extra_cost
            - self.cost_rub
        )

    @property
    def buyout_pct(self) -> float:
        from services.file_processor import compute_buyout_coef_pct

        return compute_buyout_coef_pct(self.sales_qty, self.returns_qty)


@dataclass
class WbReportModel:
    """Нормализованный отчёт WB: магазин + SKU."""

    kind: WbReportKind
    revenue: float
    sales_qty: float
    deliveries_qty: float
    returns_qty: float
    buyout_coef_pct: float
    ad_spend: float
    storage_cost: float
    credit_deductions: float
    logistics_cost: float
    commission_cost: float
    other_deductions: float
    sku_by_key: dict[tuple[str, str], WbSkuMetrics] = field(default_factory=dict)

    @property
    def drr_pct(self) -> float:
        if self.revenue <= 0 or self.ad_spend <= 0:
            return 0.0
        return self.ad_spend / self.revenue * 100.0

    @property
    def cost_of_goods(self) -> float:
        return sum(s.cost_rub for s in self.sku_by_key.values())

    def tax_usn(self, rate: float = _USN_RATE) -> float:
        return self.revenue * rate

    @property
    def operational_profit(self) -> float:
        return (
            self.revenue
            - self.cost_of_goods
            - self.storage_cost
            - self.ad_spend
            - self.logistics_cost
            - self.commission_cost
            - self.other_deductions
            - self.tax_usn()
        )

    @property
    def clear_profit(self) -> float:
        return self.operational_profit - self.credit_deductions

    def sku_metrics(self) -> tuple[WbSkuMetrics, ...]:
        return tuple(self.sku_by_key.values())


def detect_wb_report_kind(headers: list[str]) -> WbReportKind:
    if is_wb_transaction_report(headers):
        return "transaction"
    lowered = [(h or "").lower() for h in headers]
    if any(
        any(m in h for m in ("выкупили", "перечислению", "предмет", "артикул"))
        for h in lowered
    ):
        return "matrix"
    return "unknown"


def _is_total_label(label: str) -> bool:
    low = (label or "").strip().lower()
    return any(low.startswith(p) for p in _TOTAL_PREFIXES)


def _match_col(headers: list[str], hints: tuple[str, ...], *, require_qty: bool = False) -> int | None:
    qty_markers = ("шт", "кол-во", "количество", "единиц")
    for idx, header in enumerate(headers):
        low = (header or "").lower()
        if not any(h in low for h in hints):
            continue
        if require_qty and not any(q in low for q in qty_markers):
            continue
        return idx
    return None


def _sum_col(matrix: list[list[str]], col: int | None) -> float:
    if col is None:
        return 0.0
    total = 0.0
    for row in matrix[1:]:
        if col < len(row):
            total += safe_float(row[col])
    return total


def _sum_promo_ad_columns(matrix: list[list[str]], headers: list[str]) -> float:
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
    return sum(abs(_sum_col(matrix, c)) for c in promo_cols)


def _tx_bucket_to_sku(sb: WbSkuTxBucket) -> WbSkuMetrics:
    return WbSkuMetrics(
        name=sb.name,
        article_id=sb.article_id,
        revenue=sb.revenue,
        sales_qty=sb.sales_qty,
        deliveries_qty=sb.deliveries_qty or sb.sales_qty,
        returns_qty=sb.returns_qty,
        logistics=sb.logistics,
        commission=sb.commission,
        ad_cost=sb.ad_cost,
        cost_rub=sb.cost_rub,
    )


def _enrich_tx_cost_stock(
    matrix: list[list[str]],
    sku_by_key: dict[tuple[str, str], WbSkuMetrics],
) -> None:
    cols = resolve_wb_tx_columns(matrix[0])
    if cols is None:
        return
    cost_col = _match_col(matrix[0], _MATRIX_COST_HINTS)
    stock_col = _match_col(matrix[0], ("остаток", "склад", "stock"))
    name_hints = ("предмет", "наименование", "номенклатур", "бренд", "товар")
    article_hints = ("артикул", "vendor", "sku", "barcode")
    name_col = _match_col(matrix[0], name_hints)
    article_col = _match_col(matrix[0], article_hints)

    for row in matrix[1:]:
        blob = tx_row_blob(row, cols)
        doc_type = (row[cols.doc_type] if cols.doc_type is not None and cols.doc_type < len(row) else "") or ""
        if classify_wb_transaction_row(blob, doc_type=str(doc_type).strip()) != "sale":
            continue
        if cols.name is not None and cols.name < len(row):
            name = (row[cols.name] or "").strip() or "—"
        elif name_col is not None and name_col < len(row):
            name = (row[name_col] or "").strip() or "—"
        else:
            name = "—"
        if cols.article is not None and cols.article < len(row):
            article = (row[cols.article] or "").strip() or name
        elif article_col is not None and article_col < len(row):
            article = (row[article_col] or "").strip() or name
        else:
            article = name
        if not is_valid_wb_sku(name, article):
            continue
        bucket = sku_by_key.get((name, article))
        if bucket is None:
            continue
        if cost_col is not None and cost_col < len(row):
            bucket.cost_rub += abs(safe_float(row[cost_col]))
        if stock_col is not None and stock_col < len(row):
            bucket.stock_qty = max(bucket.stock_qty, max(0.0, safe_float(row[stock_col])))


def _parse_transaction_report(
    matrix: list[list[str]],
) -> WbReportModel | None:
    agg = aggregate_wb_transactions(matrix)
    if agg is None:
        return None
    sku_by_key = {
        key: _tx_bucket_to_sku(sb)
        for key, sb in agg.sku_buckets.items()
        if is_valid_wb_sku(sb.name, sb.article_id)
    }
    _enrich_tx_cost_stock(matrix, sku_by_key)
    revenue = agg.revenue_from_sales
    if revenue <= 0:
        revenue = sum(s.revenue for s in sku_by_key.values())
    return WbReportModel(
        kind="transaction",
        revenue=revenue,
        sales_qty=agg.sales_qty,
        deliveries_qty=agg.deliveries_qty,
        returns_qty=agg.returns_qty,
        buyout_coef_pct=agg.buyout_coef_pct,
        ad_spend=agg.total_advertising_cost,
        storage_cost=agg.storage_cost,
        credit_deductions=agg.credit_deductions,
        logistics_cost=agg.logistics_cost,
        commission_cost=agg.commission_cost,
        other_deductions=agg.other_deductions,
        sku_by_key=sku_by_key,
    )


def _parse_matrix_report(
    matrix: list[list[str]],
    *,
    platform: str | None,
) -> WbReportModel | None:
    from services.marketplace_platform import get_marketplace_profile

    if not matrix or len(matrix) < 2:
        return None
    profile = get_marketplace_profile(platform)
    headers = matrix[0]

    name_col = _match_col(headers, ("предмет", "наименование", "номенклатур", "бренд", "товар"))
    if name_col is None:
        name_col = 0
    article_col = _match_col(headers, ("артикул", "sku", "vendor", "barcode", "nmid"))
    rev_col = _match_col(headers, profile.revenue_hints)
    sales_col = _match_col(headers, profile.sales_hints, require_qty=True)
    if sales_col is None:
        sales_col = _match_col(headers, profile.sales_hints)
    del_col = _match_col(headers, profile.delivery_hints, require_qty=True)
    if del_col is None:
        del_col = _match_col(headers, profile.delivery_hints)
    ret_col = _match_col(headers, profile.return_hints, require_qty=True)
    if ret_col is None:
        ret_col = _match_col(headers, profile.return_hints)
    comm_col = _match_col(headers, profile.commission_hints)
    log_col = _match_col(headers, profile.logistics_hints)
    cost_col = _match_col(headers, _MATRIX_COST_HINTS)
    stock_col = _match_col(headers, profile.stock_hints)
    ad_cols = [
        idx
        for idx, h in enumerate(headers)
        if any(x in (h or "").lower() for x in profile.ad_hints)
        and "кредит" not in (h or "").lower()
        and "хранен" not in (h or "").lower()
    ]
    extra_cols = [
        idx
        for idx, h in enumerate(headers)
        if any(x in (h or "").lower() for x in profile.extra_deduction_hints)
        and idx not in ad_cols
        and idx != comm_col
        and idx != log_col
        and (cost_col is None or idx != cost_col)
    ]

    sku_by_key: dict[tuple[str, str], WbSkuMetrics] = {}
    for row in matrix[1:]:
        name = (row[name_col] if name_col < len(row) else "").strip() or "—"
        if article_col is not None and article_col < len(row):
            article = (row[article_col] or "").strip() or name
        else:
            article = name
        if _is_total_label(name):
            continue
        if not is_valid_wb_sku(name, article):
            continue
        key = (name[:64], article[:48])
        bucket = sku_by_key.get(key)
        if bucket is None:
            bucket = WbSkuMetrics(name=key[0], article_id=key[1])
            sku_by_key[key] = bucket
        if rev_col is not None and rev_col < len(row):
            val = safe_float(row[rev_col])
            if val > 0:
                bucket.revenue += val
        if sales_col is not None and sales_col < len(row):
            bucket.sales_qty += safe_float(row[sales_col])
        if del_col is not None and del_col < len(row):
            bucket.deliveries_qty += safe_float(row[del_col])
        if ret_col is not None and ret_col < len(row):
            bucket.returns_qty += safe_float(row[ret_col])
        if comm_col is not None and comm_col < len(row):
            bucket.commission += abs(safe_float(row[comm_col]))
        if log_col is not None and log_col < len(row):
            low_hdr = (headers[log_col] or "").lower()
            if "хранен" not in low_hdr:
                bucket.logistics += abs(safe_float(row[log_col]))
        for ac in ad_cols:
            if ac < len(row):
                bucket.ad_cost += abs(safe_float(row[ac]))
        for ec in extra_cols:
            if ec < len(row):
                bucket.extra_cost += abs(safe_float(row[ec]))
        if cost_col is not None and cost_col < len(row):
            bucket.cost_rub += abs(safe_float(row[cost_col]))
        if stock_col is not None and stock_col < len(row):
            bucket.stock_qty += max(0.0, safe_float(row[stock_col]))

    if not sku_by_key:
        return None

    revenue = sum(s.revenue for s in sku_by_key.values())
    if revenue <= 0 and rev_col is not None:
        revenue = sum(
            safe_float(row[rev_col])
            for row in matrix[1:]
            if rev_col < len(row) and safe_float(row[rev_col]) > 0
        )

    sales_qty = sum(s.sales_qty for s in sku_by_key.values())
    deliveries_qty = sum(s.deliveries_qty for s in sku_by_key.values())
    returns_qty = sum(s.returns_qty for s in sku_by_key.values())
    if returns_qty > 0:
        if deliveries_qty > 0:
            returns_qty = min(returns_qty, deliveries_qty)
        if sales_qty > 0:
            returns_qty = min(returns_qty, sales_qty * 2.0)

    from services.file_processor import compute_buyout_coef_pct

    buyout = compute_buyout_coef_pct(sales_qty, returns_qty)

    ad_spend = sum(s.ad_cost for s in sku_by_key.values())
    if ad_spend <= 0:
        ad_spend = _sum_promo_ad_columns(matrix, headers)

    return WbReportModel(
        kind="matrix",
        revenue=revenue,
        sales_qty=sales_qty,
        deliveries_qty=deliveries_qty,
        returns_qty=returns_qty,
        buyout_coef_pct=buyout,
        ad_spend=ad_spend,
        storage_cost=0.0,
        credit_deductions=0.0,
        logistics_cost=sum(s.logistics for s in sku_by_key.values()),
        commission_cost=sum(s.commission for s in sku_by_key.values()),
        other_deductions=sum(s.extra_cost for s in sku_by_key.values()),
        sku_by_key=sku_by_key,
    )


def parse_wb_report(
    matrix: list[list[str]],
    *,
    platform: str | None = None,
) -> WbReportModel | None:
    """
    Единый парсер WB: детализация транзакций или SKU-матрица.

    Возвращает ``None``, если отчёт пустой или формат не распознан.
    """
    if not matrix or len(matrix) < 2:
        return None
    kind = detect_wb_report_kind(matrix[0])
    if kind == "transaction":
        return _parse_transaction_report(matrix)
    if kind == "matrix":
        return _parse_matrix_report(matrix, platform=platform)
    return _parse_matrix_report(matrix, platform=platform)
