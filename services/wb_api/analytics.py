"""Локальная оцифровка WB: ABC, OOS, кассовый разрыв — 0 ₽ OpenRouter."""

from __future__ import annotations

import math
from typing import Iterable

from services.wb_api.types import (
    AbcAnalysisResult,
    AbcProductItem,
    OutOfStockForecast,
    WbBatchDigest,
    WbProductMetrics,
)

_USN_RATE = 0.06
_OOS_RISK_DAYS = 7
_TOP_A_SHARE = 0.20


def compute_product_margins(raw_rows: Iterable[dict]) -> list[WbProductMetrics]:
    """
    Считает маржу по каждому товару из нормализованного JSON WB.

    Ожидаемые ключи: sku, name, revenue, commission, logistics, ad_cost,
    stock_qty, sales_7d_qty.
    """
    out: list[WbProductMetrics] = []
    for row in raw_rows:
        sku = str(row.get("sku") or row.get("nmId") or row.get("nm_id") or "").strip()
        name = str(row.get("name") or row.get("subject") or sku or "—").strip()[:64]
        revenue = _f(row.get("revenue") or row.get("retail_amount") or row.get("forPay"))
        commission = abs(_f(row.get("commission") or row.get("ppvz_sales_commission")))
        logistics = abs(_f(row.get("logistics") or row.get("delivery_rub")))
        ad_cost = abs(_f(row.get("ad_cost") or row.get("advertising")))
        margin = revenue - commission - logistics - ad_cost
        out.append(
            WbProductMetrics(
                sku=sku or name,
                name=name,
                revenue=revenue,
                margin=margin,
                stock_qty=max(_f(row.get("stock_qty") or row.get("quantity") or row.get("stocks")), 0.0),
                sales_7d_qty=max(_f(row.get("sales_7d_qty") or row.get("sales_qty") or row.get("buyouts")), 0.0),
                ad_cost=ad_cost,
            )
        )
    return [p for p in out if p.name]


def run_abc_analysis(products: list[WbProductMetrics]) -> AbcAnalysisResult:
    """
    ABC по Парето: группа A — топ-20% SKU по выручке;
    группа C — нулевая/отрицательная маржа (неликвид).
    """
    if not products:
        return AbcAnalysisResult((), (), ())

    ranked = sorted(products, key=lambda p: (p.revenue, p.margin), reverse=True)
    n = len(ranked)
    top_a_count = max(1, math.ceil(n * _TOP_A_SHARE))
    group_a_skus = {p.sku for p in ranked[:top_a_count]}
    group_c_skus = {p.sku for p in products if p.margin <= 0}

    def _item(p: WbProductMetrics, group: str) -> AbcProductItem:
        return AbcProductItem(
            sku=p.sku,
            name=p.name,
            revenue=p.revenue,
            margin=p.margin,
            abc_group=group,
        )

    group_a = tuple(_item(p, "A") for p in products if p.sku in group_a_skus)
    group_c = tuple(_item(p, "C") for p in products if p.sku in group_c_skus)
    group_b = tuple(
        _item(p, "B")
        for p in products
        if p.sku not in group_a_skus and p.sku not in group_c_skus
    )
    return AbcAnalysisResult(group_a=group_a, group_b=group_b, group_c=group_c)


def forecast_out_of_stock(product: WbProductMetrics) -> OutOfStockForecast:
    """Дни до OOS и FOMO-упущенная выгода при risk_out_of_stock."""
    daily_sales = product.sales_7d_qty / 7.0 if product.sales_7d_qty > 0 else 0.0
    if daily_sales <= 0:
        days: float | None = None
        risk = False
        fomo = 0.0
    else:
        days = product.stock_qty / daily_sales if product.stock_qty > 0 else 0.0
        risk = days < _OOS_RISK_DAYS
        fomo = 0.0
        if risk:
            shortage_days = max(0.0, _OOS_RISK_DAYS - days)
            unit_margin = (product.margin / product.sales_7d_qty) if product.sales_7d_qty > 0 else 0.0
            if unit_margin <= 0 and product.revenue > 0:
                unit_margin = product.revenue / product.sales_7d_qty * 0.15
            fomo = max(0.0, unit_margin * daily_sales * shortage_days)

    return OutOfStockForecast(
        sku=product.sku,
        name=product.name,
        stock_qty=product.stock_qty,
        sales_7d_qty=product.sales_7d_qty,
        days_until_stockout=days,
        risk_out_of_stock=risk,
        fomo_lost_rub=fomo,
    )


def run_out_of_stock_forecasts(products: list[WbProductMetrics]) -> tuple[OutOfStockForecast, ...]:
    return tuple(forecast_out_of_stock(p) for p in products)


def estimate_cash_gap_risk(products: list[WbProductMetrics], revenue_total: float) -> bool:
    """Грубый флаг кассового разрыва: отрицательная суммарная маржа + высокая реклама."""
    if revenue_total <= 0:
        return False
    total_margin = sum(p.margin for p in products)
    total_ad = sum(p.ad_cost for p in products)
    if total_margin < 0:
        return True
    ad_load = total_ad / revenue_total * 100.0
    return ad_load > 25.0 and total_margin < revenue_total * 0.05


def build_compact_digest(
    products: list[WbProductMetrics],
    abc: AbcAnalysisResult,
    oos: tuple[OutOfStockForecast, ...],
) -> WbBatchDigest:
    """Фишка #3: сухая строка для минимального ИИ-запроса."""
    revenue = sum(p.revenue for p in products)
    total_margin = sum(p.margin for p in products)
    tax = revenue * _USN_RATE
    net_profit = total_margin - tax

    leader = abc.group_a[0].name if abc.group_a else "—"
    risky = [f for f in oos if f.risk_out_of_stock]
    risky.sort(key=lambda x: (x.days_until_stockout or 999.0))
    oos_name: str | None = None
    oos_days: int | None = None
    fomo = 0.0
    if risky:
        top = risky[0]
        oos_name = top.name
        oos_days = int(max(1, round(top.days_until_stockout or 1)))
        fomo = top.fomo_lost_rub

    cash_gap = estimate_cash_gap_risk(products, revenue)
    oos_part = (
        f"Риск OOS через {oos_days} дн.: {oos_name}, FOMO {fomo:,.0f} руб."
        if oos_name and oos_days
        else "OOS: нет критических SKU"
    )
    compact = (
        f"Лидер A: {leader}; чистая прибыль {net_profit:,.0f} руб.; "
        f"{oos_part}; кассовый разрыв={'да' if cash_gap else 'нет'}; "
        f"группа C (неликвид): {len(abc.group_c)} SKU"
    )
    return WbBatchDigest(
        compact_line=compact,
        net_profit=net_profit,
        group_a_leader=leader,
        oos_product=oos_name,
        oos_days=oos_days,
        fomo_rub=fomo,
    )


def _f(value: object) -> float:
    try:
        if value is None:
            return 0.0
        return float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return 0.0
