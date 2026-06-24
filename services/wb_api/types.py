"""Типы данных WB API nightly worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WbProductMetrics:
    """Нормализованные метрики одного SKU после локального ETL."""

    sku: str
    name: str
    revenue: float
    margin: float
    stock_qty: float
    sales_7d_qty: float
    ad_cost: float = 0.0


@dataclass(frozen=True)
class AbcProductItem:
    sku: str
    name: str
    revenue: float
    margin: float
    abc_group: str  # A | B | C


@dataclass(frozen=True)
class AbcAnalysisResult:
    group_a: tuple[AbcProductItem, ...]
    group_b: tuple[AbcProductItem, ...]
    group_c: tuple[AbcProductItem, ...]

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        def _rows(items: tuple[AbcProductItem, ...]) -> list[dict[str, Any]]:
            return [
                {
                    "sku": i.sku,
                    "name": i.name,
                    "revenue": round(i.revenue, 2),
                    "margin": round(i.margin, 2),
                    "abc_group": i.abc_group,
                }
                for i in items
            ]

        return {
            "group_a": _rows(self.group_a),
            "group_b": _rows(self.group_b),
            "group_c": _rows(self.group_c),
        }


@dataclass(frozen=True)
class OutOfStockForecast:
    sku: str
    name: str
    stock_qty: float
    sales_7d_qty: float
    days_until_stockout: float | None
    risk_out_of_stock: bool
    fomo_lost_rub: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "name": self.name,
            "stock_qty": round(self.stock_qty, 2),
            "sales_7d_qty": round(self.sales_7d_qty, 2),
            "days_until_stockout": (
                round(self.days_until_stockout, 1) if self.days_until_stockout is not None else None
            ),
            "risk_out_of_stock": self.risk_out_of_stock,
            "fomo_lost_rub": round(self.fomo_lost_rub, 2),
        }


@dataclass(frozen=True)
class WbBatchDigest:
    """Сжатая строка для утреннего ИИ-инсайта (минимум токенов)."""

    compact_line: str
    net_profit: float
    group_a_leader: str
    oos_product: str | None
    oos_days: int | None
    fomo_rub: float
    morning_insight: str = ""


@dataclass
class WbProcessedReport:
    """Итог обработки одного пользователя."""

    user_id: int
    report_id: int
    table_json: str
    digest: WbBatchDigest
    abc: AbcAnalysisResult
    oos_forecasts: tuple[OutOfStockForecast, ...] = field(default_factory=tuple)
