"""Классификация строк еженедельного отчёта WB (детализация транзакций)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from services.file_processor import compute_buyout_coef_pct, find_column_index
from services.table_number_parse import safe_float

WbTxKind = Literal[
    "sale",
    "return",
    "credit",
    "storage",
    "ad",
    "logistics",
    "commission",
    "storno",
    "penalty",
    "acceptance",
    "other",
    "skip",
]

_EMPTY_SKU_MARKERS = frozenset({"—", "-", "–", "", "none", "null", "unknown"})


def is_valid_wb_sku(name: str, article_id: str) -> bool:
    """Исключает пустые прочерки «— —» из ABC и светофора."""
    n = (name or "").strip()
    a = (article_id or "").strip()
    if n.lower() in _EMPTY_SKU_MARKERS and a.lower() in _EMPTY_SKU_MARKERS:
        return False
    combined = f"{n} {a}".strip().lower()
    if combined in ("— —", "- -", "– –", "— -", "- —"):
        return False
    return bool(n and n not in _EMPTY_SKU_MARKERS) or bool(a and a not in _EMPTY_SKU_MARKERS)


def _header_low(headers: list[str]) -> list[str]:
    return [(h or "").replace("\u00a0", " ").strip().lower() for h in headers]


def is_wb_transaction_report(headers: list[str]) -> bool:
    """Детализация WB: есть тип документа или обоснование для оплаты."""
    lowered = _header_low(headers)
    if any("тип документа" in h for h in lowered):
        return True
    if any("обоснован" in h and "оплат" in h for h in lowered):
        return True
    if any(h == "обоснование для оплаты" for h in lowered):
        return True
    return False


@dataclass(frozen=True)
class WbTxColumns:
    doc_type: int | None
    justification: int | None
    name: int | None
    article: int | None
    qty: int | None
    revenue: int | None
    logistics: int | None
    commission: int | None
    deduction: int | None


def _find_col(lowered: list[str], *patterns: str, exclude: tuple[str, ...] = ()) -> int | None:
    for idx, header in enumerate(lowered):
        if not header:
            continue
        if exclude and any(ex in header for ex in exclude):
            continue
        if any(p in header for p in patterns):
            return idx
    return None


def resolve_wb_tx_columns(headers: list[str]) -> WbTxColumns | None:
    if not is_wb_transaction_report(headers):
        return None
    lowered = _header_low(headers)
    doc_type = find_column_index(headers, "operation_type")
    if doc_type is None:
        doc_type = _find_col(lowered, "тип документа")
    justification = _find_col(lowered, "обоснован")
    name = _find_col(lowered, "предмет", "наименование", "номенклатур", "бренд", "товар")
    article = _find_col(lowered, "артикул", "vendor", "sku", "barcode", "nmid")
    qty = _find_col(
        lowered,
        "кол-во",
        "количество",
        "кол во",
        exclude=("возврат", "доставк", "заказ"),
    )
    if qty is None:
        qty = _find_col(lowered, "кол")
    revenue = find_column_index(headers, "retail_price")
    if revenue is None:
        revenue = find_column_index(headers, "payout_price")
    if revenue is None:
        revenue = _find_col(
            lowered,
            "к перечислению",
            "перечислению продавцу",
            "перечислению за",
            "вайлдберриз к перечислению",
        )
    logistics = _find_col(
        lowered,
        "услуги по доставке",
        "логистик",
        "доставк",
        exclude=("хранен",),
    )
    commission = _find_col(lowered, "вознагражден", "комисс")
    deduction = _find_col(lowered, "удержан", "штраф", "компенсац")
    return WbTxColumns(
        doc_type=doc_type,
        justification=justification,
        name=name,
        article=article,
        qty=qty,
        revenue=revenue,
        logistics=logistics,
        commission=commission,
        deduction=deduction,
    )


def _cell(row: list[str], col: int | None) -> str:
    if col is None or col >= len(row):
        return ""
    return str(row[col] or "").strip()


def _row_text_blob(row: list[str], cols: WbTxColumns) -> str:
    parts = [
        _cell(row, cols.doc_type),
        _cell(row, cols.justification),
    ]
    return " ".join(parts).lower()


def classify_wb_transaction_row(blob: str, *, doc_type: str = "") -> WbTxKind:
    """Разделение удержаний: кредит / хранение / реклама / продажа / сторно / штраф."""
    from services.file_processor import classify_cfo_tx_row

    kind = classify_cfo_tx_row(blob, doc_type=doc_type)
    mapping: dict[str, WbTxKind] = {
        "sale": "sale",
        "sale_adjustment": "sale",
        "return": "return",
        "cancel": "return",
        "storno": "storno",
        "credit": "credit",
        "storage": "storage",
        "acceptance": "acceptance",
        "utilization": "storage",
        "penalty": "penalty",
        "system_loss": "credit",
        "ad": "ad",
        "forward_logistics": "logistics",
        "reverse_logistics": "logistics",
        "commission": "commission",
        "other": "other",
        "skip": "skip",
    }
    if "кредит" in f"{doc_type} {blob}".lower():
        return "credit"
    return mapping.get(kind, "other")


@dataclass
class WbSkuTxBucket:
    name: str
    article_id: str
    revenue: float = 0.0
    sales_qty: float = 0.0
    returns_qty: float = 0.0
    deliveries_qty: float = 0.0
    logistics: float = 0.0
    commission: float = 0.0
    ad_cost: float = 0.0
    cost_rub: float = 0.0


@dataclass
class WbTransactionShopAgg:
    """Агрегат магазина из детализации WB (обёртка над CFO Engine v11.1)."""

    sales_qty: float = 0.0
    deliveries_qty: float = 0.0
    returns_qty: float = 0.0
    buyout_coef_pct: float = 0.0
    revenue_from_sales: float = 0.0
    total_advertising_cost: float = 0.0
    storage_cost: float = 0.0
    credit_deductions: float = 0.0
    logistics_cost: float = 0.0
    commission_cost: float = 0.0
    other_deductions: float = 0.0
    sku_buckets: dict[tuple[str, str], WbSkuTxBucket] = field(default_factory=dict)


def aggregate_wb_transactions(
    matrix: list[list[str]],
) -> WbTransactionShopAgg | None:
    """Построчная агрегация еженедельного отчёта WB через CFO Engine v11.1."""
    from services.file_processor import aggregate_cfo_engine_v11_1

    engine = aggregate_cfo_engine_v11_1(matrix)
    if engine is None or engine.kind != "transaction":
        return None

    agg = WbTransactionShopAgg(
        sales_qty=engine.sales_qty,
        deliveries_qty=sum(
            b.deliveries_qty for b in engine.sku_buckets.values()
        ),
        returns_qty=engine.returns_qty,
        buyout_coef_pct=engine.buyout_coef_pct,
        revenue_from_sales=engine.tax_base_revenue,
        total_advertising_cost=engine.total_ad_spend,
        storage_cost=engine.total_storage_cost,
        credit_deductions=engine.credit_deductions,
        logistics_cost=engine.logistics_cost,
        commission_cost=engine.commission_cost,
        other_deductions=engine.total_system_losses,
    )
    for key, bucket in engine.sku_buckets.items():
        agg.sku_buckets[key] = WbSkuTxBucket(
            name=bucket.name,
            article_id=bucket.article_id,
            revenue=bucket.gross_sales_rrc,
            sales_qty=bucket.sales_qty,
            returns_qty=bucket.returns_qty,
            deliveries_qty=bucket.deliveries_qty,
            logistics=bucket.forward_logistics + bucket.reverse_logistics,
            commission=bucket.commission,
            cost_rub=bucket.cost_rub,
        )
    return agg
