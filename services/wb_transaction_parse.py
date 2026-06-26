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
    text = f"{doc_type} {blob}".lower().strip()
    if not text:
        return "skip"
    if "сторно" in text or "корректировк" in text:
        return "storno"
    if any(k in text for k in ("кредит", "выплата по кредиту")):
        return "credit"
    if "хранен" in text or "стоимость хранения" in text:
        return "storage"
    if "платная приемка" in text or "платная приёмка" in text:
        return "acceptance"
    if "удержание за отсутствие маркировки" in text or (
        "штраф" in text and "кредит" not in text
    ):
        return "penalty"
    if any(k in text for k in ("реклам", "продвижен", "трафарет", "спецразмещ", "медийн", "буст")):
        return "ad"
    doc = (doc_type or "").lower().strip()
    if doc == "продажа" or text.startswith("продажа"):
        return "sale"
    if "возврат" in text and "логистик" not in text:
        return "return"
    if "продаж" in text and "возврат" not in text:
        return "sale"
    if any(k in text for k in ("логистик", "доставк", "перевоз")) and "хранен" not in text:
        return "logistics"
    if any(k in text for k in ("вознагражден", "комисс")):
        return "commission"
    if any(k in text for k in ("удержан", "штраф")) and "кредит" not in text:
        if any(k in text for k in ("реклам", "продвижен")):
            return "ad"
        return "other"
    return "other"


def _money_amount(row: list[str], cols: WbTxColumns, *, kind: WbTxKind) -> float:
    """Сумма по строке: перечисление, удержание или логистика."""
    amounts: list[float] = []
    if cols.revenue is not None and cols.revenue < len(row):
        amounts.append(safe_float(row[cols.revenue]))
    if cols.deduction is not None and cols.deduction < len(row):
        amounts.append(safe_float(row[cols.deduction]))
    if kind in ("logistics", "sale", "return", "storno") and cols.logistics is not None:
        if cols.logistics < len(row):
            amounts.append(safe_float(row[cols.logistics]))
    if kind in ("commission", "sale") and cols.commission is not None:
        if cols.commission < len(row):
            amounts.append(safe_float(row[cols.commission]))
    if not amounts:
        return 0.0
    if kind == "sale":
        return max(0.0, max(amounts))
    if kind == "storno":
        return sum(amounts)
    return sum(abs(v) for v in amounts if v != 0)


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
    """Агрегат магазина из детализации WB."""

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


def _sku_identity(row: list[str], cols: WbTxColumns) -> tuple[str, str]:
    name = _cell(row, cols.name) or _cell(row, cols.article) or "—"
    article = _cell(row, cols.article) or name
    return name[:64], article[:48]


def aggregate_wb_transactions(
    matrix: list[list[str]],
) -> WbTransactionShopAgg | None:
    """
    Построчная агрегация еженедельного отчёта WB.

  Продажи — только строки «Продажа» / обоснование с фактом выкупа.
  Кредиты и хранение не попадают в ДРР.
    """
    if not matrix or len(matrix) < 2:
        return None
    headers = matrix[0]
    cols = resolve_wb_tx_columns(headers)
    if cols is None:
        return None

    agg = WbTransactionShopAgg()
    for row in matrix[1:]:
        blob = _row_text_blob(row, cols)
        doc_type = _cell(row, cols.doc_type)
        kind = classify_wb_transaction_row(blob, doc_type=doc_type)
        if kind == "skip":
            continue
        amount = _money_amount(row, cols, kind=kind)
        qty = abs(safe_float(row[cols.qty])) if cols.qty is not None and cols.qty < len(row) else 0.0

        if kind == "credit":
            agg.credit_deductions += amount
            continue
        if kind == "storage":
            agg.storage_cost += amount
            continue
        if kind == "penalty":
            agg.other_deductions += amount
            continue
        if kind == "acceptance":
            agg.other_deductions += amount
            continue
        if kind == "ad":
            agg.total_advertising_cost += amount
            continue
        if kind == "storno":
            name, article = _sku_identity(row, cols)
            if is_valid_wb_sku(name, article):
                bucket = agg.sku_buckets.get((name, article))
                if bucket is None:
                    bucket = WbSkuTxBucket(name=name, article_id=article)
                    agg.sku_buckets[(name, article)] = bucket
                bucket.revenue += amount
            continue
        if kind == "logistics":
            agg.logistics_cost += amount
        elif kind == "commission":
            agg.commission_cost += amount
        elif kind == "other":
            agg.other_deductions += amount

        name, article = _sku_identity(row, cols)
        if not is_valid_wb_sku(name, article):
            if kind == "sale":
                agg.sales_qty += qty if qty > 0 else (1.0 if amount > 0 else 0.0)
                agg.revenue_from_sales += amount
                agg.deliveries_qty += qty if qty > 0 else 1.0
            elif kind == "return":
                agg.returns_qty += qty if qty > 0 else 1.0
            continue

        bucket = agg.sku_buckets.get((name, article))
        if bucket is None:
            bucket = WbSkuTxBucket(name=name, article_id=article)
            agg.sku_buckets[(name, article)] = bucket

        if kind == "sale":
            bucket.sales_qty += qty if qty > 0 else (1.0 if amount > 0 else 0.0)
            bucket.revenue += amount
            bucket.deliveries_qty += qty if qty > 0 else 1.0
            agg.sales_qty += qty if qty > 0 else (1.0 if amount > 0 else 0.0)
            agg.revenue_from_sales += amount
            agg.deliveries_qty += qty if qty > 0 else 1.0
        elif kind == "return":
            bucket.returns_qty += qty if qty > 0 else 1.0
            agg.returns_qty += qty if qty > 0 else 1.0
        elif kind == "logistics":
            bucket.logistics += amount
        elif kind == "commission":
            bucket.commission += amount

    agg.buyout_coef_pct = compute_buyout_coef_pct(agg.sales_qty, agg.returns_qty)

    return agg
