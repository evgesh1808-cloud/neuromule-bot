"""Сборка расширенного JSON отчёта для Mini App."""

from __future__ import annotations

import json
from datetime import date

from services.wb_api.analytics import estimate_cash_gap_risk
from services.wb_api.types import (
    AbcAnalysisResult,
    OutOfStockForecast,
    WbBatchDigest,
    WbProductMetrics,
)


def build_extended_report_json(
    *,
    products: list[WbProductMetrics],
    abc: AbcAnalysisResult,
    oos_forecasts: tuple[OutOfStockForecast, ...],
    digest: WbBatchDigest,
    report_date: date | None = None,
) -> str:
    """Канонический JSON: таблица + abc_analysis + out_of_stock_forecast."""
    day = (report_date or date.today()).isoformat()
    revenue_total = sum(p.revenue for p in products)
    headers = ["SKU", "Товар", "Выручка", "Маржа", "Остаток", "Продажи 7д", "ABC"]
    abc_map = {i.sku: i.abc_group for g in (abc.group_a, abc.group_b, abc.group_c) for i in g}
    rows = [
        [
            p.sku,
            p.name,
            round(p.revenue, 2),
            round(p.margin, 2),
            round(p.stock_qty, 2),
            round(p.sales_7d_qty, 2),
            abc_map.get(p.sku, "B"),
        ]
        for p in products
    ]
    payload = {
        "title": f"WB API — утренняя аналитика {day}",
        "headers": headers,
        "rows": rows,
        "source": "wb_api_nightly_worker",
        "abc_analysis": abc.to_dict(),
        "out_of_stock_forecast": [f.to_dict() for f in oos_forecasts],
        "morning_insight": digest.morning_insight,
        "summary": {
            "net_profit": round(digest.net_profit, 2),
            "group_a_leader": digest.group_a_leader,
            "oos_product": digest.oos_product,
            "oos_days": digest.oos_days,
            "fomo_rub": round(digest.fomo_rub, 2),
            "cash_gap_risk": estimate_cash_gap_risk(products, revenue_total),
            "compact_digest": digest.compact_line,
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
