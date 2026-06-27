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

from services.file_processor import (
    CfoSkuBucket,
    aggregate_cfo_engine_v11_1,
    compute_buyout_coef_pct,
)
from services.wb_transaction_parse import is_wb_transaction_report

WbReportKind = Literal["transaction", "matrix", "unknown"]

_USN_RATE = 0.06
_TOTAL_PREFIXES = ("итого", "всего", "total")


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
            - self.return_logistics_rub
            - self.cost_rub
        )

    @property
    def buyout_pct(self) -> float:
        return compute_buyout_coef_pct(self.sales_qty, self.returns_qty)


@dataclass
class WbReportModel:
    """Нормализованный отчёт WB: магазин + SKU (CFO Engine v11.1)."""

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
    tax_total: float
    total_sku_margin: float
    clear_profit: float
    operational_profit: float
    retail_price_source: str = "rrc"
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
        return self.tax_total if self.tax_total > 0 else self.revenue * rate

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


def _cfo_bucket_to_sku(bucket: CfoSkuBucket) -> WbSkuMetrics:
    return WbSkuMetrics(
        name=bucket.name,
        article_id=bucket.article_id,
        revenue=bucket.gross_sales_rrc,
        sales_qty=bucket.sales_qty,
        deliveries_qty=bucket.deliveries_qty or bucket.sales_qty,
        returns_qty=bucket.returns_qty,
        logistics=bucket.forward_logistics,
        return_logistics_rub=bucket.reverse_logistics,
        commission=bucket.commission,
        cost_rub=bucket.cost_rub,
        stock_qty=bucket.stock_qty,
    )


def _engine_to_report(engine: object) -> WbReportModel:
    from services.file_processor import CfoEngineResult

    assert isinstance(engine, CfoEngineResult)
    sku_by_key = {key: _cfo_bucket_to_sku(b) for key, b in engine.sku_buckets.items()}
    deliveries = sum(s.deliveries_qty for s in sku_by_key.values())
    return WbReportModel(
        kind=engine.kind if engine.kind in ("transaction", "matrix") else "unknown",
        revenue=engine.tax_base_revenue,
        sales_qty=engine.sales_qty,
        deliveries_qty=deliveries,
        returns_qty=engine.returns_qty,
        buyout_coef_pct=engine.buyout_coef_pct,
        ad_spend=engine.total_ad_spend,
        storage_cost=engine.total_storage_cost,
        credit_deductions=engine.credit_deductions,
        logistics_cost=engine.logistics_cost,
        commission_cost=engine.commission_cost,
        other_deductions=engine.total_system_losses,
        tax_total=engine.tax_total,
        total_sku_margin=engine.total_sku_margin,
        clear_profit=engine.clear_profit,
        operational_profit=engine.operational_profit,
        retail_price_source=engine.retail_price_source,
        sku_by_key=sku_by_key,
    )


def parse_wb_report(
    matrix: list[list[str]],
    *,
    platform: str | None = None,
) -> WbReportModel | None:
    """
    Единый парсер WB: детализация транзакций или SKU-матрица (CFO Engine v11.1).

    Возвращает ``None``, если отчёт пустой или формат не распознан.
    """
    if not matrix or len(matrix) < 2:
        return None
    engine = aggregate_cfo_engine_v11_1(matrix, platform=platform)
    if engine is None:
        return None
    if not engine.sku_buckets and engine.tax_base_revenue <= 0:
        return None
    return _engine_to_report(engine)
