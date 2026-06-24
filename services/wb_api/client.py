"""HTTP-клиент Wildberries Seller API (нормализация JSON → ETL)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "https://statistics-api.wildberries.ru"
_STOCKS_PATH = "/api/v1/supplier/stocks"
_SALES_PATH = "/api/v1/supplier/sales"


class WbApiClient:
    """Тонкий клиент WB API с таймаутом и без ретраев на весь батч."""

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE,
        timeout_sec: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec
        self._http = http_client

    async def fetch_product_rows(self, api_token: str) -> list[dict[str, Any]]:
        """
        Загружает остатки и продажи, мержит по nmId/supplierArticle.

        При ошибке API пробрасывает исключение — воркер ловит per-user.
        """
        headers = {"Authorization": api_token.strip()}
        own_client = self._http is None
        client = self._http or httpx.AsyncClient(timeout=self._timeout)
        try:
            stocks_resp, sales_resp = await client.get(
                f"{self._base_url}{_STOCKS_PATH}",
                headers=headers,
            ), await client.get(
                f"{self._base_url}{_SALES_PATH}",
                headers=headers,
            )
            stocks_resp.raise_for_status()
            sales_resp.raise_for_status()
            stocks_raw = stocks_resp.json()
            sales_raw = sales_resp.json()
        finally:
            if own_client:
                await client.aclose()

        return merge_wb_stocks_and_sales(stocks_raw, sales_raw)


def merge_wb_stocks_and_sales(
    stocks_raw: Any,
    sales_raw: Any,
) -> list[dict[str, Any]]:
    """Склеивает JSON WB в строки для :func:`compute_product_margins`."""
    stocks = _as_list(stocks_raw)
    sales = _as_list(sales_raw)

    sales_by_key: dict[str, dict[str, float]] = {}
    for row in sales:
        if not isinstance(row, dict):
            continue
        key = _row_key(row)
        bucket = sales_by_key.setdefault(key, {"sales_7d_qty": 0.0, "revenue": 0.0})
        bucket["sales_7d_qty"] += _float(row.get("quantity") or row.get("qty") or 1)
        bucket["revenue"] += _float(
            row.get("forPay") or row.get("finishedPrice") or row.get("retail_amount")
        )

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in stocks:
        if not isinstance(row, dict):
            continue
        key = _row_key(row)
        seen.add(key)
        sales_part = sales_by_key.get(key, {})
        merged.append(
            {
                "sku": key,
                "name": row.get("subject") or row.get("supplierArticle") or key,
                "stock_qty": row.get("quantity") or row.get("quantityFull") or 0,
                "sales_7d_qty": sales_part.get("sales_7d_qty", 0.0),
                "revenue": sales_part.get("revenue", 0.0) or _float(row.get("Price")),
                "commission": row.get("commission") or 0,
                "logistics": row.get("deliveryRub") or row.get("logistics") or 0,
                "ad_cost": row.get("advertising") or 0,
            }
        )

    for key, sales_part in sales_by_key.items():
        if key in seen:
            continue
        merged.append(
            {
                "sku": key,
                "name": key,
                "stock_qty": 0,
                "sales_7d_qty": sales_part.get("sales_7d_qty", 0.0),
                "revenue": sales_part.get("revenue", 0.0),
                "commission": 0,
                "logistics": 0,
                "ad_cost": 0,
            }
        )
    return merged


def _as_list(raw: Any) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, list):
            return data
    return []


def _row_key(row: dict[str, Any]) -> str:
    for field in ("nmId", "nm_id", "supplierArticle", "sku"):
        val = row.get(field)
        if val is not None and str(val).strip():
            return str(val).strip()
    return str(row.get("barcode") or "unknown")


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
