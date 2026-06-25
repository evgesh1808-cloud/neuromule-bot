"""ИИ-консалтинг для под-режима wb_ozon_finance (метрики ETL → OpenRouter)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

from config import Settings
from content.chat_prompt import build_wb_marketplace_finance_system_prompt
from services.ai_text import ask_ai_messages
from services.table_text_response import (
    WbMarketplaceMetrics,
    _fmt_rub_in_code,
    compute_wb_marketplace_metrics,
)
from services.telegram_safe_text import repair_telegram_html

logger = logging.getLogger(__name__)

_USN_RATE = 0.06
_BALLAST_BUYOUT_PCT = 15.0
_DRR_WARNING_PCT = 20.0
_LOW_BUYOUT_PCT = 40.0
_HIGH_DRR_PCT = 18.0
_CRITICAL_BUYOUT_PCT = 5.0
_SLOW_TURNOVER_DAYS = 45.0
_ILLIQUID_MIN_STOCK = 1.0
MIN_REVERSE_LOGISTICS_RUB = 50.0


def clamp_shop_returns_qty(
    returns_qty: float,
    *,
    sales_qty: float,
    deliveries_qty: float,
) -> float:
    """Возвраты на уровне магазина: не больше доставок и не больше 2× выкупов."""
    qty = max(0.0, returns_qty)
    if qty <= 0:
        return 0.0
    if deliveries_qty > 0:
        qty = min(qty, deliveries_qty)
    if sales_qty > 0:
        qty = min(qty, sales_qty * 2.0)
    return qty


def build_mpstats_return_logistics_payload(
    matrix_etl: object | None,
    *,
    revenue_total: float,
) -> dict[str, Any]:
    """Готовые строки потерь на обратной логистике для MPSTATS JSON и промпта."""
    if matrix_etl is None or revenue_total <= 0:
        return {
            "total_loss_rub": 0.0,
            "lines": [],
            "shop_avg_rub_per_unit": 0.0,
        }
    items: list[dict[str, Any]] = []
    for line in getattr(matrix_etl, "logistics_fomo_items", ()) or ():
        text = (line or "").strip()
        if text:
            items.append({"text": text})
    block = getattr(matrix_etl, "return_logistics_block", "") or ""
    lines = [ln.lstrip("• ").strip() for ln in block.splitlines() if ln.strip()]
    return {
        "total_loss_rub": round(float(getattr(matrix_etl, "logistics_fomo_rub", 0.0) or 0.0), 2),
        "lines": items or [{"text": ln} for ln in lines],
        "shop_avg_rub_per_unit": round(
            float(getattr(matrix_etl, "reverse_logistics_shop_avg", 0.0) or 0.0), 2
        ),
        "min_tariff_rub": MIN_REVERSE_LOGISTICS_RUB,
    }


def _format_sku_label(name: str, article: str) -> str:
    """Связка «название (арт. …)» — не абстрактный бренд."""
    name = (name or "—").strip()
    art = (article or "").strip()
    if not art or art == name:
        return name
    return f"{name} (арт. {art})"


def _sku_qualifies_for_success_zone(buyout_pct: float, margin_rub: float) -> bool:
    """🟢 Зона успеха: выкуп ≥ порога, маржа > 0, выкуп не нулевой."""
    return (
        buyout_pct >= _LOW_BUYOUT_PCT
        and buyout_pct > 0
        and margin_rub > 0
    )


def _dedupe_report_noise(text: str) -> str:
    """Убирает дубли вроде «(риск OOS) (риск OOS)»."""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"(\(риск OOS\)\s*)+", "(риск OOS) ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def _sku_label_is_valid(name: str, article: str) -> bool:
    """Строка с пустым прочерком вместо имени не выводится в отчёт."""
    n = (name or "").strip()
    a = (article or "").strip()
    if n in ("—", "-", "–") and a in ("—", "-", "–", ""):
        return False
    return bool(n and n not in ("—", "-", "–")) or bool(a and a not in ("—", "-", "–"))


def _ru_more_goods_suffix(count: int) -> str:
    """«… и ещё N товара/товаров» с правильным склонением."""
    n = abs(int(count))
    if n % 10 == 1 and n % 100 != 11:
        word = "товар"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        word = "товара"
    else:
        word = "товаров"
    return f"… и ещё {n} {word}"


def _classify_group_c_problem_zone(
    buyout_pct: float,
    revenue: float,
    *,
    stock_qty: float,
    sales_qty: float,
    days_until_stockout: float | None,
) -> str:
    """«ballast» — низкий выкуп; «illiquid» — залежалый остаток без спроса."""
    if buyout_pct < _BALLAST_BUYOUT_PCT and sales_qty > 0:
        return "ballast"
    if sales_qty <= 0 and stock_qty >= _ILLIQUID_MIN_STOCK:
        return "illiquid"
    if (
        revenue <= 0
        and stock_qty >= _ILLIQUID_MIN_STOCK
        and buyout_pct <= _CRITICAL_BUYOUT_PCT
    ):
        return "illiquid"
    if (
        days_until_stockout is not None
        and days_until_stockout > _SLOW_TURNOVER_DAYS
        and sales_qty > 0
        and stock_qty >= _ILLIQUID_MIN_STOCK
    ):
        return "illiquid"
    if buyout_pct < _BALLAST_BUYOUT_PCT:
        return "ballast"
    if revenue <= 0 and buyout_pct < 55.0:
        return "ballast"
    return "ballast" if buyout_pct < 55.0 else "illiquid"


def _parse_return_logistics_from_etl(matrix_etl: object | None) -> dict[str, dict[str, float | int]]:
    """Парсит готовые строки ETL «Логистика возвратов: …» в словарь по метке SKU."""
    out: dict[str, dict[str, float | int]] = {}
    if matrix_etl is None:
        return out
    pattern = re.compile(
        r"Логистика возвратов:\s*(.+?):\s*(\d+)\s*возвратов\.\s*"
        r"Общий убыток на пустых покатушках:\s*≈\s*([\d\s.,]+)\s*руб",
        re.IGNORECASE,
    )
    block = getattr(matrix_etl, "return_logistics_block", "") or ""
    for line in block.splitlines():
        m = pattern.search(line)
        if not m:
            continue
        label = m.group(1).strip()
        loss_raw = m.group(3).replace(" ", "").replace(",", ".")
        try:
            loss = float(loss_raw)
        except ValueError:
            loss = 0.0
        out[label] = {
            "returns": int(m.group(2)),
            "loss": round(loss, 2),
        }
    return out


def _extract_problem_zones_structured(
    matrix_etl: object | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Полные списки балласта и неликвида для JSON (без усечения)."""
    if matrix_etl is None:
        return [], []
    group_c = getattr(matrix_etl, "abc_group_c", ()) or ()
    if not group_c:
        return [], []

    logistics_map = _parse_return_logistics_from_etl(matrix_etl)
    oos_map = {f.label: f for f in getattr(matrix_etl, "oos_forecasts", ()) or ()}
    catalog = {s.name: s for s in getattr(matrix_etl, "sku_catalog", ()) or ()}
    ballast: list[dict[str, Any]] = []
    non_liquid: list[dict[str, Any]] = []

    for sku in group_c:
        oos = oos_map.get(sku.name)
        stock = float(oos.stock_qty) if oos else 0.0
        sales = float(oos.sales_period_qty) if oos else 0.0
        days = oos.days_until_stockout if oos else None
        label = _format_sku_label(sku.name, sku.article_id)
        zone = _classify_group_c_problem_zone(
            sku.buyout_pct,
            sku.revenue,
            stock_qty=stock,
            sales_qty=sales,
            days_until_stockout=days,
        )
        if zone == "illiquid":
            detail = catalog.get(sku.name)
            frozen = float(detail.revenue) if detail and detail.revenue > 0 else 0.0
            non_liquid.append(
                {
                    "sku": label,
                    "stock": int(stock),
                    "frozen_capital_rub": round(frozen, 2),
                }
            )
        else:
            log = logistics_map.get(label) or logistics_map.get(sku.name) or {}
            ballast.append(
                {
                    "sku": label,
                    "buyout": round(float(sku.buyout_pct), 1),
                    "returns": int(log.get("returns", 0)),
                    "loss": round(float(log.get("loss", 0.0)), 2),
                }
            )

    ballast.sort(key=lambda x: float(x.get("buyout", 100.0)))
    non_liquid.sort(key=lambda x: -int(x.get("stock", 0)))
    return ballast, non_liquid


def _extract_problem_zone_sku_lists(matrix_etl: object | None) -> tuple[list[str], list[str]]:
    """Устаревшая обёртка: метки SKU из структурированных зон."""
    ballast, non_liquid = _extract_problem_zones_structured(matrix_etl)
    return (
        [str(item["sku"]) for item in ballast],
        [str(item["sku"]) for item in non_liquid],
    )


def _build_traffic_light_json(
    *,
    group_a: list[str],
    ballast: list[dict[str, Any]],
    drr: float,
    prompt_metrics: WbFinancePromptMetrics | None,
    wb_metrics: WbMarketplaceMetrics | None,
) -> dict[str, str]:
    """Готовые тексты светофора — модель только копирует в HTML."""
    green_parts: list[str] = []
    yellow_parts: list[str] = []
    red_parts: list[str] = []

    if group_a:
        green_parts.append("Прибыльные лидеры: " + "; ".join(group_a))
    if prompt_metrics and prompt_metrics.abc_a_leader_name:
        green_parts.append(
            f"Лидер «{prompt_metrics.abc_a_leader_name}» "
            f"(арт. {prompt_metrics.abc_a_leader_article})"
        )

    if drr > _DRR_WARNING_PCT:
        yellow_parts.append(
            f"ДРР {drr:.1f}% — Вы работаете на рекламу, а не на карман."
        )
    elif drr > 0:
        yellow_parts.append(f"ДРР {drr:.1f}% — контролируйте окупаемость рекламы.")

    if prompt_metrics and prompt_metrics.buy_ratio_pct > 0 and prompt_metrics.buy_ratio_pct < 55:
        yellow_parts.append(f"Выкуп {prompt_metrics.buy_ratio_pct:.1f}% — зона внимания.")

    for item in ballast:
        red_parts.append(
            f"«{item['sku']}» — выкуп {item['buyout']}% — "
            f"убыток на покатушках ≈ {item['loss']} руб."
        )
    if prompt_metrics and prompt_metrics.outsider_loss > 0:
        red_parts.append(
            f"«{prompt_metrics.outsider_name}» (арт. {prompt_metrics.outsider_article}) — "
            f"убыток {_fmt_rub_in_code(prompt_metrics.outsider_loss)} руб."
        )
    if wb_metrics and wb_metrics.top5_units:
        worst = min(wb_metrics.top5_units, key=lambda u: u.net_income)
        if worst.net_income < 0:
            red_parts.append(
                f"«{worst.label}» — {_fmt_rub_in_code(worst.net_income)} руб./шт."
            )

    return {
        "green": " ".join(green_parts) if green_parts else "критических утечек в лидерах не выявлено",
        "yellow": " ".join(yellow_parts) if yellow_parts else "показатели в норме",
        "red": " ".join(red_parts) if red_parts else "критических убытков не выявлено",
    }


def _build_oos_predictions_map(matrix_etl: object | None, *, max_days: int = 7) -> dict[str, int]:
    """Критические OOS: дней до обнуления остатка (≤ max_days)."""
    if matrix_etl is None:
        return {}
    catalog = {s.name: s for s in getattr(matrix_etl, "sku_catalog", ()) or ()}
    out: dict[str, int] = {}
    for forecast in getattr(matrix_etl, "oos_forecasts", ()) or ():
        days = forecast.days_until_stockout
        if days is None or days > max_days:
            continue
        if forecast.stock_qty <= 0 and forecast.sales_period_qty <= 0:
            continue
        detail = catalog.get(forecast.label)
        key = (
            _format_sku_label(detail.name, detail.article_id)
            if detail
            else forecast.label
        )
        out[key] = max(0, int(days))
    return out


_WB_XLSX_HEADER_SCAN_ROWS = 25
_WB_RETURN_COL_ID_SKIP = (
    "srid",
    "rrid",
    "rrd",
    " id",
    "id ",
    "номер",
    "код возврата",
    "документ",
    "транзак",
)
_WB_QTY_MARKERS = ("шт", "кол-во", "количество", "единиц")


def _wb_xlsx_is_return_id_header(header: str) -> bool:
    low = (header or "").lower()
    if any(q in low for q in _WB_QTY_MARKERS):
        return False
    return any(s in low for s in _WB_RETURN_COL_ID_SKIP)


def _wb_xlsx_find_col(
    headers: list[str],
    keywords: tuple[str, ...],
    *,
    require_qty: bool = False,
    skip_return_ids: bool = False,
) -> int | None:
    for idx, header in enumerate(headers):
        low = (header or "").lower()
        if not any(k.lower() in low for k in keywords):
            continue
        if require_qty and not any(q in low for q in _WB_QTY_MARKERS):
            continue
        if skip_return_ids and _wb_xlsx_is_return_id_header(header):
            continue
        return idx
    return None


def _wb_xlsx_safe_float(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _wb_xlsx_sku_label(brand: str, article: str) -> str:
    brand = (brand or "").strip()
    article = (article or "").strip()
    if not brand or brand in ("—", "-", "Unknown", "None"):
        brand = article or "—"
    if not article or article == brand:
        return brand
    return f"{brand} (арт. {article})"


def _wb_xlsx_skip_row(brand: str, article: str) -> bool:
    for label in (brand, article):
        low = (label or "").strip().lower()
        if low.startswith(("итого", "всего", "total")):
            return True
    if brand in ("None", "", "—", "-") and article in ("None", "", "—", "-"):
        return True
    return False


def _wb_xlsx_detect_header(sheet: object) -> tuple[int, list[str]] | None:
    """Ищет строку шапки WB (после преамбули поставщика)."""
    max_row = min(getattr(sheet, "max_row", 0) or 0, _WB_XLSX_HEADER_SCAN_ROWS)
    max_col = min(getattr(sheet, "max_column", 0) or 0, 80)
    revenue_keys = (
        "к перечислению",
        "вайлдберриз к перечислению",
        "продажи",
        "выруч",
    )
    identity_keys = ("бренд", "артикул", "предмет", "наименование", "номенклатур")
    for row_idx in range(1, max_row + 1):
        headers = [
            str(sheet.cell(row=row_idx, column=col).value or "").strip()
            for col in range(1, max_col + 1)
        ]
        if not any(headers):
            continue
        has_revenue = any(any(k in (h or "").lower() for k in revenue_keys) for h in headers)
        has_identity = any(any(k in (h or "").lower() for k in identity_keys) for h in headers)
        if has_revenue and has_identity:
            return row_idx, headers
    return None


def _wb_xlsx_return_loss_rub(returns_count: int, delivery_rub: float) -> float:
    if returns_count <= 0:
        return 0.0
    per_unit = delivery_rub / returns_count if delivery_rub > 0 else 0.0
    unit = max(MIN_REVERSE_LOGISTICS_RUB, per_unit)
    return round(returns_count * unit, 2)


def build_wb_weekly_xlsx_ai_context(file_path: str | Path) -> dict[str, Any] | None:
    """
    Парсер еженедельных отчётов Wildberries (openpyxl): агрегация по SKU,
    валидация возвратов, MPSTATS JSON без галлюцинаций модели.
    """
    from pathlib import Path as PathCls

    from openpyxl import load_workbook

    path = PathCls(file_path)
    if not path.is_file():
        return None

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = wb.active
        detected = _wb_xlsx_detect_header(sheet)
        if detected is None:
            return None
        header_row, headers = detected

        col_brand = _wb_xlsx_find_col(headers, ("бренд", "brand"))
        col_art = _wb_xlsx_find_col(
            headers, ("артикул поставщика", "артикул", "vendor", "sku", "barcode")
        )
        col_name = _wb_xlsx_find_col(headers, ("предмет", "наименование", "номенклатур", "товар"))
        col_sales = _wb_xlsx_find_col(
            headers,
            (
                "вайлдберриз к перечислению",
                "к перечислению за товар",
                "к перечислению",
                "продажи",
                "выруч",
            ),
        )
        col_cost = _wb_xlsx_find_col(headers, ("себестоимость", "закупка"))
        col_delivery = _wb_xlsx_find_col(
            headers,
            ("услуги по доставке", "логистика", "стоимость логистики", "доставк"),
        )
        col_returns_qty = _wb_xlsx_find_col(
            headers,
            ("количество возвратов", "возврат штук", "возвраты"),
            require_qty=True,
            skip_return_ids=True,
        )
        if col_returns_qty is None:
            col_returns_qty = _wb_xlsx_find_col(
                headers, ("возврат",), require_qty=True, skip_return_ids=True
            )
        col_orders_qty = _wb_xlsx_find_col(
            headers,
            ("количество заказов", "заказы штук", "заказы", "заказано"),
            require_qty=True,
        )
        if col_orders_qty is None:
            col_orders_qty = _wb_xlsx_find_col(
                headers, ("доставк", "выкупили"), require_qty=True
            )
        col_ad_spend = _wb_xlsx_find_col(
            headers, ("расходы на рекламу", "реклама", "внутренняя реклама", "продвижен", "удержан")
        )
        col_stock = _wb_xlsx_find_col(headers, ("текущий остаток", "остаток на складе", "остатк"))
        col_daily_sales = _wb_xlsx_find_col(
            headers, ("средние продажи в день", "скорость продаж")
        )

        if col_sales is None:
            return None

        def cell(row_idx: int, col_idx: int | None) -> object:
            if col_idx is None:
                return None
            return sheet.cell(row=row_idx, column=col_idx + 1).value

        sku_data: dict[str, dict[str, float | int]] = {}
        max_row = getattr(sheet, "max_row", header_row) or header_row

        for row_idx in range(header_row + 1, max_row + 1):
            brand = str(cell(row_idx, col_brand) or cell(row_idx, col_name) or "").strip()
            article = str(cell(row_idx, col_art) or cell(row_idx, col_name) or brand).strip()
            if _wb_xlsx_skip_row(brand, article):
                continue

            sku_label = _wb_xlsx_sku_label(brand, article)
            sales = _wb_xlsx_safe_float(cell(row_idx, col_sales))
            cost = _wb_xlsx_safe_float(cell(row_idx, col_cost))
            delivery = abs(_wb_xlsx_safe_float(cell(row_idx, col_delivery)))
            returns_qty = int(_wb_xlsx_safe_float(cell(row_idx, col_returns_qty)))
            orders_qty = int(_wb_xlsx_safe_float(cell(row_idx, col_orders_qty)))
            ad_spend = abs(_wb_xlsx_safe_float(cell(row_idx, col_ad_spend)))
            stock = int(_wb_xlsx_safe_float(cell(row_idx, col_stock)))
            daily_sales = _wb_xlsx_safe_float(cell(row_idx, col_daily_sales))

            if sales == 0 and returns_qty == 0 and orders_qty == 0 and stock == 0:
                continue

            bucket = sku_data.setdefault(
                sku_label,
                {
                    "revenue": 0.0,
                    "cost": 0.0,
                    "delivery": 0.0,
                    "returns_count": 0,
                    "orders_count": 0,
                    "ad_spend": 0.0,
                    "stock": 0,
                    "daily_sales": 0.0,
                },
            )
            bucket["revenue"] = float(bucket["revenue"]) + sales
            bucket["cost"] = float(bucket["cost"]) + cost
            bucket["delivery"] = float(bucket["delivery"]) + delivery
            bucket["returns_count"] = int(bucket["returns_count"]) + returns_qty
            bucket["orders_count"] = int(bucket["orders_count"]) + orders_qty
            bucket["ad_spend"] = float(bucket["ad_spend"]) + ad_spend
            bucket["stock"] = max(int(bucket["stock"]), stock)
            bucket["daily_sales"] = max(float(bucket["daily_sales"]), daily_sales)

        if not sku_data:
            return None

        for metrics in sku_data.values():
            orders = int(metrics["orders_count"])
            returns = int(metrics["returns_count"])
            if orders > 0:
                metrics["returns_count"] = min(returns, orders)
            metrics["returns_count"] = int(
                clamp_shop_returns_qty(
                    float(metrics["returns_count"]),
                    sales_qty=float(orders),
                    deliveries_qty=float(orders),
                )
            )

        total_revenue = sum(float(m["revenue"]) for m in sku_data.values())
        if total_revenue <= 0:
            return None

        total_ad_spend = sum(float(m["ad_spend"]) for m in sku_data.values())
        tax_usn = round(total_revenue * _USN_RATE, 2)

        sorted_skus: list[dict[str, Any]] = []
        total_profit = 0.0
        return_logistics_lines: list[str] = []
        total_return_loss = 0.0

        for sku, metrics in sku_data.items():
            orders_count = int(metrics["orders_count"])
            returns_count = int(metrics["returns_count"])
            delivery_rub = float(metrics["delivery"])
            revenue = float(metrics["revenue"])
            cost = float(metrics["cost"])
            ad_spend = float(metrics["ad_spend"])

            total_actions = orders_count + returns_count
            buyout_rate = (
                (orders_count / total_actions * 100.0) if total_actions > 0 else 100.0
            )
            return_loss_rub = _wb_xlsx_return_loss_rub(returns_count, delivery_rub)
            total_return_loss += return_loss_rub
            if returns_count > 0 and return_loss_rub > 0:
                loss_s = f"{return_loss_rub:,.2f}".replace(",", " ")
                return_logistics_lines.append(
                    f"Логистика возвратов: {sku}: {returns_count} возвратов. "
                    f"Общий убыток на пустых покатушках: ≈ {loss_s} руб."
                )

            sku_profit = revenue - cost - delivery_rub - (revenue * _USN_RATE) - ad_spend
            total_profit += sku_profit
            sorted_skus.append(
                {
                    "sku": sku,
                    "revenue": revenue,
                    "profit": sku_profit,
                    "buyout_rate": buyout_rate,
                    "returns_count": returns_count,
                    "return_loss_rub": return_loss_rub,
                    "stock": int(metrics["stock"]),
                    "daily_sales": float(metrics["daily_sales"]),
                }
            )

        sorted_skus.sort(key=lambda x: x["revenue"], reverse=True)

        group_a: list[str] = []
        group_c: list[str] = []
        ballast: list[dict[str, Any]] = []
        non_liquid: list[dict[str, Any]] = []
        oos_predictions: dict[str, int] = {}

        running_sum = 0.0
        for item in sorted_skus:
            running_sum += item["revenue"]
            if running_sum <= total_revenue * 0.8 and item["profit"] > 0:
                group_a.append(item["sku"])
            else:
                group_c.append(item["sku"])

            if item["buyout_rate"] < _BALLAST_BUYOUT_PCT and item["returns_count"] > 0:
                ballast.append(
                    {
                        "sku": item["sku"],
                        "buyout": round(item["buyout_rate"], 1),
                        "returns": item["returns_count"],
                        "loss": round(item["return_loss_rub"], 2),
                    }
                )
            if item["stock"] > 0 and item["revenue"] == 0:
                non_liquid.append({"sku": item["sku"], "stock": item["stock"]})
            if item["daily_sales"] > 0:
                days_left = int(item["stock"] / item["daily_sales"])
                if days_left <= 7:
                    oos_predictions[item["sku"]] = days_left

        drr = (total_ad_spend / total_revenue * 100.0) if total_revenue > 0 else 0.0
        business_score = 10.0
        if total_profit <= 0:
            business_score -= 4.0
        if ballast:
            business_score -= 2.0
        if oos_predictions:
            business_score -= 1.5
        if drr > _DRR_WARNING_PCT:
            business_score -= 1.5
        business_score = max(1.0, min(10.0, business_score))

        margin_rate = (total_profit / total_revenue * 100.0) if total_revenue > 0 else 0.0
        shop_avg = (
            total_return_loss / sum(int(m["returns_count"]) for m in sku_data.values())
            if sum(int(m["returns_count"]) for m in sku_data.values()) > 0
            else 0.0
        )
        emoji, status = _business_score_band(business_score)
        verdict = derive_business_verdict(
            business_score=business_score,
            profitability_pct=margin_rate,
            ad_load_pct=drr,
            buyout_coef_pct=0.0,
            worst_unit_label=group_c[0] if group_c else None,
        )
        reason = (
            "📉 Балл занижен из-за балласта и возвратов."
            if ballast
            else "📈 Операционные показатели в пределах нормы."
        )
        traffic_light = {
            "green": (
                "Прибыльные лидеры: " + "; ".join(group_a)
                if group_a
                else "лидеры не выделены"
            ),
            "yellow": (
                f"ДРР {drr:.1f}% — Вы работаете на рекламу, а не на карман."
                if drr > _DRR_WARNING_PCT
                else f"ДРР {drr:.1f}%"
            ),
            "red": (
                "; ".join(
                    f"«{b['sku']}» — убыток ≈ {b['loss']} руб." for b in ballast
                )
                if ballast
                else "критических убытков не выявлено"
            ),
        }

        return {
            "finance": {
                "total_revenue": round(total_revenue, 2),
                "tax_usn": tax_usn,
                "total_profit": round(total_profit, 2),
                "margin_rate": round(margin_rate, 1),
                "drr": round(drr, 1),
                "business_score": round(business_score, 1),
            },
            "health_index": {
                "score": round(business_score, 1),
                "emoji": emoji,
                "status": status,
                "reason": reason,
                "verdict": verdict,
            },
            "abc_analysis": {
                "group_A": group_a
                if group_a
                else ["Лидеры отсутствуют, вся матрица требует санации"],
                "group_C": group_c,
                "total_group_c_count": len(group_c),
            },
            "problem_zones": {
                "ballast": ballast,
                "non_liquid": non_liquid,
            },
            "traffic_light": traffic_light,
            "loss_calculator": {
                "return_logistics": {
                    "total_loss_rub": round(total_return_loss, 2),
                    "lines": [{"text": ln} for ln in return_logistics_lines],
                    "shop_avg_rub_per_unit": round(shop_avg, 2),
                    "min_tariff_rub": MIN_REVERSE_LOGISTICS_RUB,
                },
                "fomo_lost_rub": round(total_return_loss, 2),
            },
            "oos_predictions": oos_predictions,
            "year_forecast_rub": round(total_revenue * 52, 0),
            "localization_index": "не указан в исходных данных",
            "sku_catalog": [
                {
                    "sku": sku,
                    "revenue": round(float(m["revenue"]), 2),
                    "returns_count": int(m["returns_count"]),
                    "orders_count": int(m["orders_count"]),
                }
                for sku, m in sku_data.items()
            ],
            "parser": "wb_weekly_openpyxl_v1",
        }
    finally:
        wb.close()


def build_wb_mpstats_ai_context(
    matrix_rows: list[list[str]],
    *,
    revenue_total: float,
    platform: str | None = "wildberries",
) -> dict[str, Any]:
    """
    MPSTATS-стиль JSON для OpenRouter: финансы, ABC, проблемные зоны, OOS.

    Использует локальный ETL (openpyxl → матрица), без pandas.
    """
    from services.file_processor import compute_seller_matrix_etl

    if revenue_total <= 0 or not matrix_rows or len(matrix_rows) < 2:
        return {"error": "empty_or_no_revenue"}

    etl = compute_seller_matrix_etl(matrix_rows, revenue_total=revenue_total, platform=platform)
    wb_metrics = resolve_wb_metrics_for_rows(matrix_rows, revenue_total, platform=platform)
    prompt_metrics = compute_wb_finance_prompt_metrics(
        revenue_total,
        wb_metrics,
        matrix_rows=matrix_rows,
        platform=platform,
    )

    group_a: list[str] = []
    group_c_all: list[str] = []
    if etl:
        group_a = [_format_sku_label(s.name, s.article_id) for s in etl.abc_group_a]
        group_c_all = [_format_sku_label(s.name, s.article_id) for s in etl.abc_group_c]
    if not group_a and revenue_total > 0 and etl and etl.sku_catalog:
        leader = max(etl.sku_catalog, key=lambda s: s.revenue)
        group_a = [_format_sku_label(leader.name, leader.article_id)]
    if not group_a:
        group_a = ["Лидеры отсутствуют, требуется оптимизация"]

    ballast, non_liquid = _extract_problem_zones_structured(etl)
    drr = wb_metrics.ad_load_pct if wb_metrics else 0.0
    tax = revenue_total * _USN_RATE
    margin_rate = prompt_metrics.profitability_pct if prompt_metrics else 0.0
    clear_profit = prompt_metrics.clear_profit if prompt_metrics else revenue_total - tax
    business_score = prompt_metrics.business_score if prompt_metrics else 0.0
    verdict = prompt_metrics.verdict if prompt_metrics else ""
    year_forecast = prompt_metrics.year_forecast if prompt_metrics else revenue_total * 12

    loc_line = prompt_metrics.localization_index_line if prompt_metrics else "не указан в исходных данных"
    traffic_light = _build_traffic_light_json(
        group_a=group_a,
        ballast=ballast,
        drr=drr,
        prompt_metrics=prompt_metrics,
        wb_metrics=wb_metrics,
    )
    emoji, status = _business_score_band(business_score)
    reason = (
        _business_score_reason_line(prompt_metrics, wb_metrics)
        if prompt_metrics
        else "Причина оценки рассчитана по операционным метрикам."
    )

    return {
        "parser": "wb_matrix_etl_v1",
        "finance": {
            "total_revenue": round(revenue_total, 2),
            "tax_usn": round(tax, 2),
            "total_profit": round(clear_profit, 2),
            "margin_rate": round(margin_rate, 1),
            "drr": round(drr, 1),
            "business_score": round(business_score, 1),
        },
        "health_index": {
            "score": round(business_score, 1),
            "emoji": emoji,
            "status": status,
            "reason": reason,
            "verdict": verdict,
        },
        "abc_analysis": {
            "group_A": group_a,
            "group_C": group_c_all,
            "total_group_c_count": len(group_c_all),
        },
        "problem_zones": {
            "ballast": ballast,
            "non_liquid": non_liquid,
        },
        "traffic_light": traffic_light,
        "loss_calculator": {
            "return_logistics": build_mpstats_return_logistics_payload(
                etl, revenue_total=revenue_total
            ),
            "fomo_lost_rub": round(prompt_metrics.fomo_lost_rub, 2) if prompt_metrics else 0.0,
        },
        "oos_predictions": _build_oos_predictions_map(etl),
        "year_forecast_rub": round(year_forecast, 0),
        "localization_index": loc_line,
        "sku_catalog": list(prompt_metrics.sku_catalog_items) if prompt_metrics else [],
    }


def resolve_wb_revenue_total(
    *,
    calculated_total: float,
    file_path: str | Path | None = None,
    matrix_rows: list[list[str]] | None = None,
    platform: str | None = "wildberries",
) -> float:
    """Выручка для CFO: worker → openpyxl JSON → preprocess матрицы."""
    if calculated_total > 0:
        return float(calculated_total)
    if file_path is not None:
        try:
            raw = prepare_wb_data_for_ai(file_path, platform=platform or "wildberries")
            data = json.loads(raw)
            rev = float((data.get("finance") or {}).get("total_revenue") or 0.0)
            if rev > 0:
                return rev
        except Exception:
            logger.debug("resolve_wb_revenue_total: file parse failed", exc_info=True)
    if matrix_rows:
        from services.table_xlsx_preprocess import preprocess_xlsx_rows

        pre = preprocess_xlsx_rows(matrix_rows, title="WB")
        if pre.revenue_total > 0:
            return float(pre.revenue_total)
    return 0.0


def _is_publishable_wb_finance_html(html: str) -> bool:
    """Ответ OpenRouter пригоден к показу (не пустой и похож на CFO-отчёт)."""
    sample = (html or "").strip()
    if len(sample) < 80:
        return False
    low = sample.lower()
    return (
        "финансовый экспресс" in low
        or "abc-анализ" in low
        or "индекс здоровья" in low
    )


def _build_local_wb_finance_html(
    revenue_total: float,
    wb_metrics: WbMarketplaceMetrics | None,
    *,
    matrix_rows: list[list[str]] | None = None,
    platform: str | None = None,
) -> str | None:
    """Гарантированный локальный CFO-отчёт (без OpenRouter)."""
    if revenue_total <= 0:
        return None
    prompt_metrics = compute_wb_finance_prompt_metrics(
        revenue_total,
        wb_metrics,
        matrix_rows=matrix_rows,
        platform=platform,
    )
    if prompt_metrics is None:
        return None
    return append_wb_finance_mini_app_cta(
        build_wb_finance_express_html_local(prompt_metrics, wb_metrics)
    )


def resolve_wb_mpstats_context(
    *,
    file_path: str | Path | None = None,
    matrix_rows: list[list[str]] | None = None,
    revenue_total: float = 0.0,
    platform: str | None = "wildberries",
    title: str | None = None,
) -> dict[str, Any]:
    """Единая точка: Excel или матрица → MPSTATS JSON-словарь (все расчёты в Python)."""
    if file_path is not None:
        try:
            raw = prepare_wb_data_for_ai(file_path, platform=platform or "wildberries", title=title)
            data = json.loads(raw)
            if isinstance(data, dict) and not data.get("error"):
                return data
        except Exception:
            logger.exception("resolve_wb_mpstats_context: prepare_wb_data_for_ai failed")
    rev = float(revenue_total or 0.0)
    if matrix_rows and len(matrix_rows) >= 2:
        if rev <= 0:
            from services.table_xlsx_preprocess import preprocess_xlsx_rows

            pre = preprocess_xlsx_rows(matrix_rows, title=title or "WB")
            rev = float(pre.revenue_total or 0.0)
        if rev > 0:
            return build_wb_mpstats_ai_context(
                matrix_rows,
                revenue_total=rev,
                platform=platform,
            )
    return {"error": "empty_or_no_revenue"}


def build_wb_finance_json_user_message(json_payload: str | dict[str, Any]) -> str:
    """User-сообщение OpenRouter: только готовый JSON-пакет."""
    if isinstance(json_payload, dict):
        body = json.dumps(json_payload, ensure_ascii=False, indent=2)
    else:
        body = json_payload.strip()
    return (
        "Ниже — подтверждённый JSON-пакет финансовой оцифровки Wildberries. "
        "Все числа, списки товаров, балласт, неликвид и бизнес-скоринг рассчитаны в Python. "
        "Строго упакуй JSON в HTML-отчёт по структуре из system prompt. "
        "Запрещено менять числа, дополнять товары, сокращать списки и использовать слово «ИИ».\n\n"
        f"{body}"
    )


def prepare_wb_data_for_ai(
    file_path: str | Path,
    *,
    platform: str = "wildberries",
    title: str | None = None,
) -> str:
    """
    Считывает Excel Wildberries, считает метрики в стиле MPSTATS и возвращает JSON для OpenRouter.

    Сначала — парсер еженедельного отчёта WB (openpyxl, агрегация по SKU),
    при неудаче — матричный ETL из preprocess_xlsx_rows.
    """
    from pathlib import Path as PathCls

    from services.file_processor import read_xlsx_rows_from_path
    from services.table_xlsx_preprocess import preprocess_xlsx_rows

    path = PathCls(file_path)

    if platform in (None, "", "wildberries"):
        weekly = build_wb_weekly_xlsx_ai_context(path)
        if weekly is not None:
            return json.dumps(weekly, ensure_ascii=False, indent=2)

    raw_rows = read_xlsx_rows_from_path(path)
    display_title = title or path.stem or "Отчёт WB"
    pre = preprocess_xlsx_rows(raw_rows, title=display_title)
    context = build_wb_mpstats_ai_context(
        pre.rows,
        revenue_total=float(pre.revenue_total or 0.0),
        platform=platform,
    )
    return json.dumps(context, ensure_ascii=False, indent=2)


def _format_sku_bullet_lines(
    buyout_pct: float,
    revenue: float,
    *,
    stock_qty: float,
    sales_qty: float,
    days_until_stockout: float | None,
) -> str:
    """«ballast» — низкий выкуп; «illiquid» — залежалый остаток без спроса."""
    if buyout_pct < _BALLAST_BUYOUT_PCT and sales_qty > 0:
        return "ballast"
    if sales_qty <= 0 and stock_qty >= _ILLIQUID_MIN_STOCK:
        return "illiquid"
    if (
        revenue <= 0
        and stock_qty >= _ILLIQUID_MIN_STOCK
        and buyout_pct <= _CRITICAL_BUYOUT_PCT
    ):
        return "illiquid"
    if (
        days_until_stockout is not None
        and days_until_stockout > _SLOW_TURNOVER_DAYS
        and sales_qty > 0
        and stock_qty >= _ILLIQUID_MIN_STOCK
    ):
        return "illiquid"
    if buyout_pct < _BALLAST_BUYOUT_PCT:
        return "ballast"
    if revenue <= 0 and buyout_pct < 55.0:
        return "ballast"
    return "ballast" if buyout_pct < 55.0 else "illiquid"


def _ballast_reason(buyout_pct: float) -> str:
    return (
        f"выкуп {buyout_pct:.1f}% (менее {_BALLAST_BUYOUT_PCT:.0f}%) — "
        "покатушки и пустая обратная логистика съедают маржу"
    )


def _illiquid_reason(
    *,
    stock_qty: float,
    sales_qty: float,
    days_until_stockout: float | None,
    revenue: float,
) -> str:
    if sales_qty <= 0 and stock_qty > 0:
        stock_s = f"{stock_qty:.0f}".replace(",", " ")
        return f"нет продаж за период, на складе {stock_s} шт. — капитал заморожен"
    if days_until_stockout is not None and days_until_stockout > _SLOW_TURNOVER_DAYS:
        return (
            f"медленная оборачиваемость (~{days_until_stockout:.0f} дн. запаса), "
            f"выручка {_fmt_rub_in_code(revenue)} руб."
        )
    stock_s = f"{stock_qty:.0f}".replace(",", " ")
    return f"залежалый остаток ({stock_s} шт.) без достаточного спроса"


def build_matrix_problem_zones_block(matrix_etl: object | None) -> str:
    """Текст подсказки ETL: балласт и неликвид из группы C."""
    if matrix_etl is None:
        return "проблемных зон в группе C не выявлено"
    group_c = getattr(matrix_etl, "abc_group_c", ()) or ()
    if not group_c:
        return "проблемных зон в группе C не выявлено"

    oos_map = {f.label: f for f in getattr(matrix_etl, "oos_forecasts", ()) or ()}
    ballast: list[tuple[float, str, str]] = []
    illiquid: list[tuple[float, str, str]] = []

    for sku in group_c:
        oos = oos_map.get(sku.name)
        stock = float(oos.stock_qty) if oos else 0.0
        sales = float(oos.sales_period_qty) if oos else 0.0
        days = oos.days_until_stockout if oos else None
        label = _format_sku_label(sku.name, sku.article_id)
        zone = _classify_group_c_problem_zone(
            sku.buyout_pct,
            sku.revenue,
            stock_qty=stock,
            sales_qty=sales,
            days_until_stockout=days,
        )
        if zone == "illiquid":
            illiquid.append(
                (
                    stock,
                    label,
                    _illiquid_reason(
                        stock_qty=stock,
                        sales_qty=sales,
                        days_until_stockout=days,
                        revenue=sku.revenue,
                    ),
                )
            )
        else:
            ballast.append((sku.buyout_pct, label, _ballast_reason(sku.buyout_pct)))

    ballast.sort(key=lambda x: x[0])
    illiquid.sort(key=lambda x: -x[0])

    lines: list[str] = []
    if ballast:
        lines.append("📉 <b>Балласт (Деньги уходят на пустые покатушки):</b>")
        for _, label, reason in ballast:
            lines.append(f"• {label} — {reason}")
    if illiquid:
        lines.append("❄️ <b>Неликвид (Капитал заморожен на складе):</b>")
        for _, label, reason in illiquid:
            lines.append(f"• {label} — {reason}")
    return "\n".join(lines) if lines else "проблемных зон в группе C не выявлено"


def _format_sku_bullet_lines(
    items: Iterable[tuple[str, str]],
    *,
    max_items: int | None = None,
    overflow_suffix: str | None = None,
) -> str:
    """Список товаров маркерами «•», по одному на строку (без сокращений по умолчанию)."""
    lines: list[str] = []
    batch = [(n, a) for n, a in items if _sku_label_is_valid(n, a)]
    display = batch if max_items is None else batch[:max_items]
    for name, article in display:
        lines.append(f"• {_format_sku_label(name, article)}")
    if overflow_suffix:
        lines.append(overflow_suffix)
    elif max_items is not None and len(batch) > max_items:
        lines.append(_ru_more_goods_suffix(len(batch) - max_items))
    return "\n".join(lines) if lines else "• убыточных товаров не выявлено"


def _expand_fomo_breakdown(parts: tuple[str, ...]) -> tuple[str, ...]:
    """Разбивает строки с «;» на отдельные пункты для маркированного списка."""
    expanded: list[str] = []
    for part in parts:
        chunk = (part or "").strip()
        if not chunk:
            continue
        if chunk.startswith("Логистика невыкупленных:"):
            chunk = chunk.removeprefix("Логистика невыкупленных:").strip()
        if chunk.startswith("Логистика возвратов:"):
            expanded.append(chunk)
            continue
        if "; " in chunk:
            expanded.extend(p.strip() for p in chunk.split("; ") if p.strip())
        else:
            expanded.append(chunk)
    return tuple(expanded)


def _leader_buyout_is_healthy(buyout_pct: float, margin_rub: float = 1.0) -> bool:
    return _sku_qualifies_for_success_zone(buyout_pct, margin_rub)


def _format_fomo_details_block(parts: tuple[str, ...]) -> str:
    expanded = _expand_fomo_breakdown(parts)
    if not expanded:
        return "• критических зон упущенной выгоды не выявлено"
    return "\n".join(f"• {p}" for p in expanded[:6])


def _build_strategic_plan_lines(
    prompt_metrics: WbFinancePromptMetrics,
    wb_metrics: WbMarketplaceMetrics | None,
) -> list[str]:
    """Три шага плана с учётом выкупа лидера и уровня ДРР."""
    leader_label = _escape_verdict(
        _format_sku_label(
            prompt_metrics.abc_a_leader_name,
            prompt_metrics.abc_a_leader_article,
        )
    )
    lines: list[str] = []

    leader_ok = _leader_buyout_is_healthy(
        prompt_metrics.abc_a_leader_buyout,
        prompt_metrics.abc_a_leader_margin,
    )

    if leader_ok:
        lines.append(
            f"<b>1.</b> Усилить закуп и рекламу на <b>{leader_label}</b> — "
            f"лидер A, выкуп <code>{prompt_metrics.abc_a_leader_buyout:.1f}%</code>, "
            f"чистая прибыль с одной продажи: <code>{_fmt_rub_in_code(prompt_metrics.abc_a_leader_margin)}</code> руб."
        )
    elif wb_metrics and wb_metrics.top5_units:
        scale_candidate = None
        for unit in sorted(wb_metrics.top5_units, key=lambda u: u.net_income, reverse=True):
            if unit.net_income > 0:
                scale_candidate = unit
                break
        if scale_candidate and leader_ok is False:
            lines.append(
                f"<b>1.</b> Не масштабируйте <b>{leader_label}</b> "
                f"(выкуп <code>{prompt_metrics.abc_a_leader_buyout:.1f}%</code>) — "
                f"сначала карточка и логистика. Рабочий драйвер: "
                f"<b>{_escape_verdict(scale_candidate.label)}</b> "
                f"(<code>{_fmt_rub_in_code(scale_candidate.net_income)}</code>/шт.)."
            )
        else:
            lines.append(
                "<b>1.</b> Не масштабируйте товары с нулевым выкупом или убытком — "
                "сначала поднимите конверсию карточки и цену."
            )
    else:
        lines.append(
            f"<b>1.</b> Не масштабируйте <b>{leader_label}</b>: "
            f"выкуп <code>{prompt_metrics.abc_a_leader_buyout:.1f}%</code> — "
            "сначала карточка, цена и логистика."
        )

    if prompt_metrics.adv_load_pct >= 30:
        lines.append(
            f"<b>2.</b> Катастрофический ДРР <code>{prompt_metrics.adv_load_pct:.1f}%</code> — "
            "немедленно режьте рекламный бюджет минимум вдвое, отключите все неокупаемые кампании "
            "и пересоберите семантику под маржинальные товары."
        )
    elif prompt_metrics.adv_load_pct > _HIGH_DRR_PCT:
        lines.append(
            f"<b>2.</b> Срочно <b>снизить ДРР</b> с <code>{prompt_metrics.adv_load_pct:.1f}%</code> "
            "до 12–15%: отключите неокупаемые кампании, сузьте ключевые слова и ставки."
        )
    elif prompt_metrics.outsider_name not in ("—", "") and prompt_metrics.outsider_loss > 0:
        out_name = _escape_verdict(_format_sku_label(prompt_metrics.outsider_name, prompt_metrics.outsider_article))
        lines.append(
            f"<b>2.</b> Для <b>{out_name}</b> поднимите цену "
            f"или остановите рекламу — убыток "
            f"<code>{_fmt_rub_in_code(prompt_metrics.outsider_loss)}</code> руб."
        )
    else:
        lines.append(
            f"<b>2.</b> Держите ДРР не выше <code>15%</code> — еженедельно чистите неокупаемые ключи."
        )

    lines.append(
        f"<b>3.</b> Контролируйте остатки по рисковым артикулам: "
        f"<i>{_escape_verdict(_dedupe_report_noise(prompt_metrics.oos_forecast_line))}</i>"
    )
    return lines
_WB_FINANCE_MAX_OUTPUT_TOKENS = 1400
_WB_FINANCE_TELEGRAM_SOFT_MAX_CHARS = 2000
_FINANCE_SEPARATOR = "────────────────────────"
# Меняйте при каждом релизе CFO-шаблона — видно внизу отчёта для проверки деплоя.
_FINANCE_REPORT_BUILD = "cfo-v8"
_OLD_FINALE_MARKERS = (
    "Финальный Excel",
    "интерактивный дашборд",
    "Автопилот по API",
    "Хватит загружать",
)
_LEGACY_FINANCE_MARKERS = (
    "ИИ-ПЛАН",
    "ИИ-Инсайт",
    "ИИ-ИНСАЙТ",
    "Senior ИИ",
    "Серверный расчёт",
    "Серверный",
    "локальный ETL",
    "ИИ-Моделирование",
)
_LEGACY_FINANCE_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"ИИ[\s\-–—]*План\s*действий", re.IGNORECASE), "ПЛАН ДЕЙСТВИЙ ДЛЯ ПРЕДПРИНИМАТЕЛЯ НА СЕГОДНЯ"),
    (re.compile(r"ИИ[\s\-–—]*Инсайт", re.IGNORECASE), "ГЛАВНЫЙ АНАЛИТИЧЕСКИЙ ВЫВОД"),
    (re.compile(r"КЛЮЧЕВОЙ\s+БИЗНЕС-ВЕРДИКТ", re.IGNORECASE), "ГЛАВНЫЙ АНАЛИТИЧЕСКИЙ ВЫВОД"),
    (re.compile(r"ГЛАВНЫЙ\s+ВЫВОД\s+ИИ", re.IGNORECASE), "ГЛАВНЫЙ АНАЛИТИЧЕСКИЙ ВЫВОД"),
    (re.compile(r"ВАЛОВАЯ\s+ВЫРУЧКА", re.IGNORECASE), "ОБЩАЯ ВЫРУЧКА"),
    (re.compile(r"Серверный\s+расч[её]т", re.IGNORECASE), "Потенциально можно вернуть в оборот"),
    (re.compile(r"ABC[\s\-–—]*АНАЛИЗ\s+МАТРИЦЫ\s*\(\s*локальный\s+ETL[^)]*\)", re.IGNORECASE), "ABC-АНАЛИЗ ПРОДАЖ"),
    (re.compile(r"ABC[\s\-–—]*АНАЛИЗ\s+МАТРИЦЫ", re.IGNORECASE), "ABC-АНАЛИЗ ПРОДАЖ"),
    (re.compile(r"РЕЙТИНГ\s+ПРОДАЖ\s+ПО\s+ТОВАРАМ", re.IGNORECASE), "ABC-АНАЛИЗ ПРОДАЖ"),
    (re.compile(r"БИЗНЕС-СКОРИНГ\s+МАГАЗИНА", re.IGNORECASE), "ИНДЕКС ЗДОРОВЬЯ БИЗНЕСА"),
    (re.compile(r"ИНДЕКС\s+ЗДОРОВЬЯ\s+МАГАЗИНА", re.IGNORECASE), "ИНДЕКС ЗДОРОВЬЯ БИЗНЕСА"),
    (re.compile(r"СВЕТОФОР\s+ЗДОРОВЬЯ\s+БИЗНЕСА", re.IGNORECASE), "СВЕТОФОР ЭФФЕКТИВНОСТИ"),
    (re.compile(r"Проблемные\s+зоны\s+матрицы", re.IGNORECASE), "Проблемные зоны и скрытые убытки"),
    (re.compile(r"ПРОГНОЗ\s+И\s+КРЭШ-ТЕСТ", re.IGNORECASE), "ПРОГНОЗ И ОБНУЛЕНИЕ ОСТАТКОВ"),
    (re.compile(r"СТРАТЕГИЧЕСКИЙ\s+ПЛАН\s+ДЕЙСТВИЙ", re.IGNORECASE), "ПЛАН ДЕЙСТВИЙ ДЛЯ ПРЕДПРИНИМАТЕЛЯ"),
    (re.compile(r"\bOOS\b", re.IGNORECASE), "Обнуление остатков на складе"),
    (re.compile(r"риск\s+OOS", re.IGNORECASE), "риск обнуления остатков"),
)
_TECH_HINT_PAREN_RE = re.compile(
    r"\(\s*[^)]*(?:"
    r"локальный\s+ETL|не\s+пересчитывай|"
    r"по\s+2[–\-]3\s+предложен|"
    r"каждый\s+шаг|каждый\s+SKU|каждый\s+источник|"
    r"маркер\s+«•»|"
    r"без\s+воды|"
    r"строго\s+из\s+шаблона"
    r")[^)]*\)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WbFinancePromptMetrics:
    """Числовые метрики для подстановки в системный промпт."""

    revenue: float
    tax: float
    clear_profit: float
    adv_load_pct: float
    buy_ratio_pct: float
    year_forecast: float
    profitability_pct: float
    business_score: float
    verdict: str
    fomo_lost_rub: float
    fomo_breakdown: tuple[str, ...]
    logistics_fomo_rub: float = 0.0
    abc_a_leader: str = "—"
    abc_a_leader_name: str = "—"
    abc_a_leader_article: str = "—"
    abc_a_leader_revenue: float = 0.0
    abc_a_leader_margin: float = 0.0
    abc_a_leader_buyout: float = 0.0
    abc_a_count: int = 0
    abc_c_count: int = 0
    abc_c_summary: str = "нет"
    outsider_name: str = "—"
    outsider_article: str = "—"
    outsider_loss: float = 0.0
    outsider_buyout: float = 0.0
    sku_catalog_lines: tuple[str, ...] = ()
    sku_catalog_items: tuple[dict[str, Any], ...] = ()
    oos_forecast_line: str = "данных по остаткам недостаточно"
    total_ad_cost: float = 0.0
    sales_qty: float = 0.0
    returns_qty: float = 0.0
    deliveries_qty: float = 0.0
    reverse_logistics_shop_avg: float = 0.0
    return_logistics_block: str = "• существенных потерь на обратной логистике не выявлено"
    matrix_problem_zones_block: str = "проблемных зон в группе C не выявлено"
    localization_index_line: str = "не указан в исходных данных"


def compute_business_score(
    *,
    profitability_pct: float,
    ad_load_pct: float,
    buyout_coef_pct: float,
    worst_unit_net: float | None,
) -> float:
    """Бизнес-скоринг 1.0–10.0 на основе рентабельности и операционных рисков."""
    score = 5.0
    if profitability_pct >= 18:
        score += 2.5
    elif profitability_pct >= 12:
        score += 1.5
    elif profitability_pct >= 7:
        score += 0.5
    elif profitability_pct < 4:
        score -= 2.0
    elif profitability_pct < 7:
        score -= 0.5

    if buyout_coef_pct >= 72:
        score += 1.0
    elif buyout_coef_pct >= 58:
        score += 0.3
    elif 0 < buyout_coef_pct < 45:
        score -= 1.5
    elif 0 < buyout_coef_pct < 55:
        score -= 0.7

    if ad_load_pct > 28:
        score -= 1.5
    elif ad_load_pct > 20:
        score -= 0.8
    elif 0 < ad_load_pct <= 12:
        score += 0.4

    if worst_unit_net is not None and worst_unit_net < 0:
        score -= 1.2

    return round(max(1.0, min(10.0, score)), 1)


def _business_score_band(score: float) -> tuple[str, str]:
    """Эмодзи и человекочитаемый статус по шкале 1–10."""
    if score < 5.0:
        return (
            "🔴",
            "КРИТИЧЕСКИЙ УРОВЕНЬ — Высокий риск кассового разрыва",
        )
    if score < 8.0:
        return (
            "🟡",
            "НОРМАЛЬНЫЙ УРОВЕНЬ — Требуется оптимизация расходов",
        )
    return "🟢", "ОТЛИЧНЫЙ УРОВЕНЬ — Эффективное управление"


def _business_score_reason_line(
    prompt_metrics: WbFinancePromptMetrics,
    wb_metrics: WbMarketplaceMetrics | None,
) -> str:
    """Одна строка причины занижения/завышения балла."""
    reasons_low: list[str] = []
    reasons_high: list[str] = []

    if prompt_metrics.profitability_pct < 7:
        reasons_low.append("низкой рентабельности")
    elif prompt_metrics.profitability_pct >= 18:
        reasons_high.append("высокой маржинальности")

    if 0 < prompt_metrics.buy_ratio_pct < 45:
        reasons_low.append("критического выкупа")
    elif prompt_metrics.buy_ratio_pct >= 72:
        reasons_high.append("стабильного выкупа")

    if prompt_metrics.adv_load_pct > 20:
        reasons_low.append("раздутого ДРР")
    elif 0 < prompt_metrics.adv_load_pct <= 12:
        reasons_high.append("контролируемой рекламы")

    if prompt_metrics.outsider_loss > 0:
        reasons_low.append("убыточных SKU")

    if wb_metrics and wb_metrics.top5_units:
        worst = min(wb_metrics.top5_units, key=lambda u: u.net_income)
        if worst.net_income < 0:
            reasons_low.append("убыточных позиций в матрице")

    if prompt_metrics.business_score >= 7.0 and reasons_high:
        return f"📈 Балл высокий благодаря {', '.join(reasons_high)}."
    if reasons_low:
        return f"📉 Балл занижен из-за {', '.join(reasons_low)}."
    if prompt_metrics.business_score >= 8.0:
        return "📈 Балл высокий благодаря сбалансированной экономике магазина."
    return "📉 Балл занижен из-за операционных рисков — нужна оптимизация."


def derive_business_verdict(
    *,
    business_score: float,
    profitability_pct: float,
    ad_load_pct: float,
    buyout_coef_pct: float,
    worst_unit_label: str | None,
) -> str:
    """Ёмкий вердикт для блока бизнес-скоринга."""
    if business_score >= 8.0:
        return "Высокая маржинальность — фокус на масштабировании лидеров ассортимента."
    if business_score >= 6.5:
        if ad_load_pct > 22:
            return "Стабильная база при риске перерасхода на рекламу и просадки кассы."
        return "Здоровый бизнес с точками роста в операционной эффективности."
    if profitability_pct >= 10 and buyout_coef_pct > 0 and buyout_coef_pct < 52:
        return "Высокая маржинальность при критическом риске кассового разрыва из-за низкого выкупа."
    if worst_unit_label and ad_load_pct > 20:
        return (
            f"Критический риск кассового разрыва: «{worst_unit_label[:32]}» и реклама "
            f"вымывают оборотные средства."
        )
    if profitability_pct < 5:
        return "Критически низкая рентабельность — угроза кассового разрыва в ближайшем цикле."
    return "Требуется жёсткая оптимизация убыточных SKU, логистики и рекламного ДРР."


def compute_fomo_lost_rub(
    revenue_total: float,
    wb_metrics: WbMarketplaceMetrics | None,
) -> tuple[float, tuple[str, ...]]:
    """
    Калькулятор упущенной выгоды (руб.): низкий выкуп, реклама, убыточные SKU.

    Возвращает ``(сумма, расшифровка для промпта)``.
    """
    if revenue_total <= 0 or wb_metrics is None:
        return 0.0, ()

    parts: list[str] = []
    total = 0.0

    buyout = wb_metrics.buyout_coef_pct
    if buyout > 0 and buyout < 65:
        gap = 65.0 - buyout
        buyout_loss = revenue_total * (gap / 100.0) * 0.22
        if buyout_loss > 50:
            total += buyout_loss
            parts.append(
                f"низкий выкуп {buyout:.1f}% → логистика возвратов и покатушки ≈ "
                f"{_fmt_rub_in_code(buyout_loss)} руб."
            )

    if wb_metrics.returns_qty > 0 and wb_metrics.deliveries_qty > 0:
        validated_returns = clamp_shop_returns_qty(
            wb_metrics.returns_qty,
            sales_qty=wb_metrics.sales_qty,
            deliveries_qty=wb_metrics.deliveries_qty,
        )
        ret_rate = validated_returns / wb_metrics.deliveries_qty
        if ret_rate > 0.12 and validated_returns > 0:
            unit = max(MIN_REVERSE_LOGISTICS_RUB, revenue_total * ret_rate * 0.08 / validated_returns)
            penalty = validated_returns * unit
            if penalty > 30:
                total += penalty
                parts.append(
                    f"возвраты {validated_returns:.0f} шт. → обратная логистика ≈ "
                    f"{_fmt_rub_in_code(penalty)} руб."
                )

    if wb_metrics.ad_load_pct > 18 and wb_metrics.total_advertising_cost > 0:
        waste = wb_metrics.total_advertising_cost * min(0.45, (wb_metrics.ad_load_pct - 15) / 100.0)
        if waste > 50:
            total += waste
            parts.append(
                f"рекламный перерасход ДРР {wb_metrics.ad_load_pct:.1f}% ≈ "
                f"{_fmt_rub_in_code(waste)} руб."
            )

    for unit in wb_metrics.top5_units:
        if unit.net_income < 0:
            sku_loss = abs(unit.net_income) * max(8.0, wb_metrics.sales_qty / max(len(wb_metrics.top5_units), 1))
            if sku_loss > 20:
                total += sku_loss
                parts.append(
                    f"неликвид «{unit.label[:28]}» (убыток {_fmt_rub_in_code(unit.net_income)}/шт.) ≈ "
                    f"{_fmt_rub_in_code(sku_loss)} руб."
                )
                break

    return round(total, 2), tuple(parts)


def compute_wb_finance_prompt_metrics(
    revenue_total: float,
    wb_metrics: WbMarketplaceMetrics | None = None,
    *,
    matrix_rows: list[list[str]] | None = None,
    platform: str | None = None,
) -> WbFinancePromptMetrics | None:
    """Собирает переменные ETL для system/user prompt и локального fallback."""
    if revenue_total <= 0:
        return None

    from services.file_processor import compute_seller_matrix_etl

    matrix_etl = (
        compute_seller_matrix_etl(
            matrix_rows,
            revenue_total=revenue_total,
            platform=platform,
        )
        if matrix_rows
        else None
    )
    tax = revenue_total * _USN_RATE
    clear_profit = revenue_total - tax
    profitability = (clear_profit / revenue_total * 100.0) if revenue_total > 0 else 0.0
    adv_load = wb_metrics.ad_load_pct if wb_metrics else 0.0
    buy_ratio = wb_metrics.buyout_coef_pct if wb_metrics else 0.0
    worst_unit = None
    worst_label = None
    if wb_metrics and wb_metrics.top5_units:
        worst_unit = min(wb_metrics.top5_units, key=lambda u: u.net_income)
        if worst_unit.net_income < 0:
            worst_label = worst_unit.label
    business_score = compute_business_score(
        profitability_pct=profitability,
        ad_load_pct=adv_load,
        buyout_coef_pct=buy_ratio,
        worst_unit_net=worst_unit.net_income if worst_unit else None,
    )
    verdict = derive_business_verdict(
        business_score=business_score,
        profitability_pct=profitability,
        ad_load_pct=adv_load,
        buyout_coef_pct=buy_ratio,
        worst_unit_label=worst_label,
    )
    fomo_rub, fomo_parts = compute_fomo_lost_rub(revenue_total, wb_metrics)
    logistics_fomo = 0.0
    abc_a_leader = "—"
    abc_a_leader_name = "—"
    abc_a_leader_article = "—"
    abc_a_leader_revenue = 0.0
    abc_a_leader_margin = 0.0
    abc_a_leader_buyout = 0.0
    abc_a_count = 0
    abc_c_count = 0
    abc_c_summary = "нет"
    matrix_problem_zones_block = "проблемных зон в группе C не выявлено"
    localization_index_line = "не указан в исходных данных"
    outsider_name = "—"
    outsider_article = "—"
    outsider_loss = 0.0
    outsider_buyout = 0.0
    sku_catalog_lines: tuple[str, ...] = ()
    sku_catalog_items: tuple[dict[str, Any], ...] = ()
    oos_line = "данных по остаткам недостаточно"
    reverse_logistics_shop_avg = 0.0
    return_logistics_block = "• существенных потерь на обратной логистике не выявлено"
    if matrix_etl:
        logistics_fomo = matrix_etl.logistics_fomo_rub
        reverse_logistics_shop_avg = matrix_etl.reverse_logistics_shop_avg
        return_logistics_block = matrix_etl.return_logistics_block
        if matrix_etl.logistics_fomo_rub > 0:
            fomo_rub = round(fomo_rub + logistics_fomo, 2)
            if matrix_etl.logistics_fomo_items:
                fomo_parts = _expand_fomo_breakdown(
                    (*fomo_parts, *matrix_etl.logistics_fomo_items)
                )
            else:
                fomo_parts = _expand_fomo_breakdown(
                    (*fomo_parts, matrix_etl.logistics_fomo_detail)
                )
        if matrix_etl.abc_group_a:
            leader = matrix_etl.abc_group_a[0]
            abc_a_leader = leader.name
            abc_a_leader_name = leader.name
            abc_a_leader_article = leader.article_id
            abc_a_leader_revenue = leader.revenue
            abc_a_leader_margin = leader.net_profit
            abc_a_leader_buyout = leader.buyout_pct
        else:
            abc_a_leader = matrix_etl.abc_a_leader
            abc_a_leader_name = matrix_etl.abc_a_leader
            abc_a_leader_article = matrix_etl.abc_a_leader
        abc_a_count = len(matrix_etl.abc_group_a)
        abc_c_count = len(matrix_etl.abc_group_c)
        if matrix_etl.abc_group_c:
            abc_c_summary = _format_sku_bullet_lines(
                [(s.name, s.article_id) for s in matrix_etl.abc_group_c],
            )
        elif abc_c_count == 0:
            abc_c_summary = "убыточных товаров не выявлено"
        matrix_problem_zones_block = build_matrix_problem_zones_block(matrix_etl)
        if matrix_etl.outsider_sku:
            outsider_name = matrix_etl.outsider_sku.name
            outsider_article = matrix_etl.outsider_sku.article_id
            outsider_loss = abs(matrix_etl.outsider_sku.net_profit)
            outsider_buyout = matrix_etl.outsider_sku.buyout_pct
        elif matrix_etl.abc_group_c:
            worst = min(matrix_etl.abc_group_c, key=lambda s: s.net_profit)
            outsider_name = worst.name
            outsider_article = worst.article_id
            outsider_loss = abs(worst.net_profit)
            outsider_buyout = worst.buyout_pct
        if matrix_etl.sku_catalog:
            sku_catalog_lines = tuple(s.catalog_line() for s in matrix_etl.sku_catalog[:20])
            sku_catalog_items = tuple(
                {
                    "name": s.name,
                    "article_id": s.article_id,
                    "revenue_rub": s.revenue,
                    "margin_rub": s.net_profit,
                    "buyout_pct": s.buyout_pct,
                    "abc_group": s.abc_group,
                }
                for s in matrix_etl.sku_catalog[:20]
            )
            gross_net = sum(s.net_profit for s in matrix_etl.sku_catalog)
            clear_profit = round(gross_net - tax, 2)
            profitability = (
                (clear_profit / revenue_total * 100.0) if revenue_total > 0 else 0.0
            )
            business_score = compute_business_score(
                profitability_pct=profitability,
                ad_load_pct=adv_load,
                buyout_coef_pct=buy_ratio,
                worst_unit_net=worst_unit.net_income if worst_unit else None,
            )
            verdict = derive_business_verdict(
                business_score=business_score,
                profitability_pct=profitability,
                ad_load_pct=adv_load,
                buyout_coef_pct=buy_ratio,
                worst_unit_label=worst_label,
            )
        if matrix_etl.oos_critical_sku and matrix_etl.oos_critical_days is not None:
            oos_line = _dedupe_report_noise(
                f"«{matrix_etl.oos_critical_sku}» — "
                f"обнуление остатков через {matrix_etl.oos_critical_days:.0f} дн."
            )
        elif matrix_etl.oos_forecasts:
            oos_line = "критических рисков обнуления остатков не выявлено"

    return WbFinancePromptMetrics(
        revenue=revenue_total,
        tax=tax,
        clear_profit=clear_profit,
        adv_load_pct=adv_load,
        buy_ratio_pct=buy_ratio,
        year_forecast=revenue_total * 12,
        profitability_pct=profitability,
        business_score=business_score,
        verdict=verdict,
        fomo_lost_rub=fomo_rub,
        fomo_breakdown=fomo_parts,
        logistics_fomo_rub=logistics_fomo,
        abc_a_leader=abc_a_leader,
        abc_a_leader_name=abc_a_leader_name,
        abc_a_leader_article=abc_a_leader_article,
        abc_a_leader_revenue=abc_a_leader_revenue,
        abc_a_leader_margin=abc_a_leader_margin,
        abc_a_leader_buyout=abc_a_leader_buyout,
        abc_a_count=abc_a_count,
        abc_c_count=abc_c_count,
        abc_c_summary=abc_c_summary,
        matrix_problem_zones_block=matrix_problem_zones_block,
        localization_index_line=localization_index_line,
        outsider_name=outsider_name,
        outsider_article=outsider_article,
        outsider_loss=outsider_loss,
        outsider_buyout=outsider_buyout,
        sku_catalog_lines=sku_catalog_lines,
        sku_catalog_items=sku_catalog_items,
        oos_forecast_line=oos_line,
        total_ad_cost=wb_metrics.total_advertising_cost if wb_metrics else 0.0,
        sales_qty=wb_metrics.sales_qty if wb_metrics else 0.0,
        returns_qty=wb_metrics.returns_qty if wb_metrics else 0.0,
        deliveries_qty=wb_metrics.deliveries_qty if wb_metrics else 0.0,
        reverse_logistics_shop_avg=reverse_logistics_shop_avg,
        return_logistics_block=return_logistics_block,
    )


def _prompt_kwargs_from_metrics(metrics: WbFinancePromptMetrics) -> dict[str, str]:
    catalog_block = (
        "\n".join(f"• {line}" for line in metrics.sku_catalog_lines)
        if metrics.sku_catalog_lines
        else "—"
    )
    return {
        "revenue": _fmt_rub_in_code(metrics.revenue),
        "tax": _fmt_rub_in_code(metrics.tax),
        "clear_profit": _fmt_rub_in_code(metrics.clear_profit),
        "profitability_pct": f"{metrics.profitability_pct:.1f}",
        "adv_load": f"{metrics.adv_load_pct:.1f}",
        "buy_ratio": f"{metrics.buy_ratio_pct:.1f}",
        "year_forecast": _fmt_rub_in_code(metrics.year_forecast, decimals=0),
        "business_score": f"{metrics.business_score:.1f}",
        "verdict": metrics.verdict,
        "fomo_lost_rub": _fmt_rub_in_code(metrics.fomo_lost_rub),
        "logistics_fomo_rub": _fmt_rub_in_code(metrics.logistics_fomo_rub),
        "abc_a_leader": metrics.abc_a_leader,
        "abc_a_leader_name": metrics.abc_a_leader_name,
        "abc_a_leader_article": metrics.abc_a_leader_article,
        "abc_a_count": str(metrics.abc_a_count),
        "abc_c_count": str(metrics.abc_c_count),
        "abc_c_summary": metrics.abc_c_summary,
        "matrix_problem_zones_block": metrics.matrix_problem_zones_block,
        "outsider_name": metrics.outsider_name,
        "outsider_article": metrics.outsider_article,
        "outsider_loss": _fmt_rub_in_code(metrics.outsider_loss),
        "outsider_buyout": f"{metrics.outsider_buyout:.1f}",
        "abc_a_leader_buyout": f"{metrics.abc_a_leader_buyout:.1f}",
        "sku_catalog_block": catalog_block,
        "fomo_details_block": _format_fomo_details_block(metrics.fomo_breakdown),
        "return_logistics_block": metrics.return_logistics_block,
        "reverse_logistics_avg_rub": f"{metrics.reverse_logistics_shop_avg:.2f}",
        "oos_forecast_line": metrics.oos_forecast_line,
        "localization_index_line": metrics.localization_index_line,
    }


def build_wb_marketplace_finance_payload_dict(
    prompt_metrics: WbFinancePromptMetrics,
    wb_metrics: WbMarketplaceMetrics | None,
) -> dict[str, Any]:
    """JSON-словарь user-сообщения для OpenRouter (wb_ozon_finance)."""
    top5: list[dict[str, Any]] = []
    traffic_light: dict[str, str] = {}
    if wb_metrics:
        top5 = [
            {
                "label": row.label,
                "sale_price": round(row.sale_price, 2),
                "unit_logistics": round(row.unit_logistics, 2),
                "net_income_per_unit": round(row.net_income, 2),
            }
            for row in wb_metrics.top5_units
        ]
        if wb_metrics.top5_units:
            best = max(wb_metrics.top5_units, key=lambda u: u.net_income)
            worst = min(wb_metrics.top5_units, key=lambda u: u.net_income)
            traffic_light["green_scale"] = (
                f"Лидер «{best.label}» — чистый доход {best.net_income:.2f} руб./шт."
            )
            if wb_metrics.ad_load_pct <= 15:
                traffic_light["green_ad"] = f"ДРР {wb_metrics.ad_load_pct:.1f}% под контролем."
            if 45 <= wb_metrics.buyout_coef_pct < 65:
                traffic_light["yellow_buyout"] = (
                    f"Выкуп {wb_metrics.buyout_coef_pct:.1f}% — на грани, нужна доработка карточек."
                )
            elif 10 < wb_metrics.ad_load_pct <= 22:
                traffic_light["yellow_ad"] = (
                    f"Реклама {wb_metrics.ad_load_pct:.1f}% — следите за окупаемостью кампаний."
                )
            if worst.net_income < 0:
                traffic_light["red_sku"] = (
                    f"«{worst.label}» убыточен: {worst.net_income:.2f} руб./шт. — вымывает оборотку."
                )
            if wb_metrics.buyout_coef_pct > 0 and wb_metrics.buyout_coef_pct < 45:
                traffic_light["red_buyout"] = (
                    f"Выкуп {wb_metrics.buyout_coef_pct:.1f}% — критическая зона возвратов."
                )

    return {
        "etl_source": "wb_ozon_finance_report",
        "revenue_rub": round(prompt_metrics.revenue, 2),
        "tax_usn_6pct_rub": round(prompt_metrics.tax, 2),
        "clear_profit_rub": round(prompt_metrics.clear_profit, 2),
        "profitability_pct": round(prompt_metrics.profitability_pct, 1),
        "business_score": prompt_metrics.business_score,
        "business_verdict": prompt_metrics.verdict,
        "fomo_lost_rub": round(prompt_metrics.fomo_lost_rub, 2),
        "fomo_breakdown": list(prompt_metrics.fomo_breakdown),
        "logistics_fomo_rub": round(prompt_metrics.logistics_fomo_rub, 2),
        "abc_analysis": {
            "group_a_leader_name": prompt_metrics.abc_a_leader_name,
            "group_a_leader_article": prompt_metrics.abc_a_leader_article,
            "group_a_leader_revenue_rub": round(prompt_metrics.abc_a_leader_revenue, 2),
            "group_a_leader_margin_rub": round(prompt_metrics.abc_a_leader_margin, 2),
            "group_a_leader_buyout_pct": round(prompt_metrics.abc_a_leader_buyout, 1),
            "group_a_count": prompt_metrics.abc_a_count,
            "group_c_count": prompt_metrics.abc_c_count,
            "group_c_summary": prompt_metrics.abc_c_summary,
            "matrix_problem_zones": prompt_metrics.matrix_problem_zones_block,
            "localization_index": prompt_metrics.localization_index_line,
        },
        "outsider_sku": {
            "name": prompt_metrics.outsider_name,
            "article_id": prompt_metrics.outsider_article,
            "loss_rub": round(prompt_metrics.outsider_loss, 2),
            "buyout_pct": round(prompt_metrics.outsider_buyout, 1),
        },
        "sku_catalog": list(prompt_metrics.sku_catalog_items),
        "out_of_stock_forecast": prompt_metrics.oos_forecast_line,
        "ad_load_pct": round(prompt_metrics.adv_load_pct, 1),
        "buyout_coef_pct": round(prompt_metrics.buy_ratio_pct, 1),
        "year_forecast_rub": round(prompt_metrics.year_forecast, 0),
        "total_advertising_cost_rub": round(prompt_metrics.total_ad_cost, 2),
        "sales_qty": prompt_metrics.sales_qty,
        "returns_qty": prompt_metrics.returns_qty,
        "deliveries_qty": prompt_metrics.deliveries_qty,
        "top5_unit_economics": top5,
        "traffic_light_hints": traffic_light,
        "local_insights": list(wb_metrics.insight_lines) if wb_metrics else [],
    }


def build_wb_marketplace_finance_user_prompt(
    prompt_metrics: WbFinancePromptMetrics,
    wb_metrics: WbMarketplaceMetrics | None,
    *,
    matrix_rows: list[list[str]] | None = None,
) -> str:
    """User-сообщение: MPSTATS JSON (все метрики из Python ETL)."""
    ctx = build_wb_mpstats_ai_context(
        matrix_rows or [],
        revenue_total=prompt_metrics.revenue,
        platform="wildberries",
    )
    if ctx.get("error"):
        ctx = build_wb_marketplace_finance_payload_dict(prompt_metrics, wb_metrics)
    return build_wb_finance_json_user_message(ctx)


def build_wb_finance_system_prompt_from_totals(
    revenue_total: float,
    wb_metrics: WbMarketplaceMetrics | None = None,
    *,
    matrix_rows: list[list[str]] | None = None,
) -> str | None:
    """Удобная обёртка: revenue + wb_metrics → готовый system prompt."""
    metrics = compute_wb_finance_prompt_metrics(
        revenue_total, wb_metrics, matrix_rows=matrix_rows
    )
    if metrics is None:
        return None
    return build_wb_marketplace_finance_system_prompt(**_prompt_kwargs_from_metrics(metrics))


def build_wb_finance_openrouter_prompt_pair(
    matrix_rows: list[list[str]],
    *,
    revenue_total: float,
    wb_metrics: WbMarketplaceMetrics | None = None,
    file_path: str | Path | None = None,
    platform: str | None = "wildberries",
) -> tuple[str, str] | None:
    """Пара system + user для OpenRouter: cfo-v8 + MPSTATS JSON из Python."""
    from content.chat_prompt import WB_ANALYTICS_SYSTEM_PROMPT

    ctx = resolve_wb_mpstats_context(
        file_path=file_path,
        matrix_rows=matrix_rows,
        revenue_total=revenue_total,
        platform=platform,
    )
    if ctx.get("error"):
        return None
    system = WB_ANALYTICS_SYSTEM_PROMPT
    user = build_wb_finance_json_user_message(ctx)
    return system, user


def has_legacy_wb_finance_markers(text: str) -> bool:
    """True, если в тексте остались устаревшие маркеры CFO-отчёта."""
    sample = (text or "").strip()
    if not sample:
        return True
    return any(marker.lower() in sample.lower() for marker in _LEGACY_FINANCE_MARKERS)


def sanitize_wb_finance_html(html: str) -> str:
    """Нормализует устаревшие заголовки и формулировки в ответе модели."""
    text = (html or "").strip()
    for pattern, replacement in _LEGACY_FINANCE_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    text = _TECH_HINT_PAREN_RE.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _abc_sku_to_dict(sku: object, group: str) -> dict[str, Any]:
    """MatrixAbcSku / MatrixSkuDetail → JSON для Mini App."""
    name = getattr(sku, "name", "—")
    article = getattr(sku, "article_id", "") or name
    revenue = float(getattr(sku, "revenue", 0) or 0)
    margin = float(getattr(sku, "net_profit", getattr(sku, "margin", 0)) or 0)
    buyout = float(getattr(sku, "buyout_pct", 0) or 0)
    return {
        "sku": str(article),
        "name": str(name),
        "article_id": str(article),
        "revenue": round(revenue, 2),
        "revenue_rub": round(revenue, 2),
        "margin": round(margin, 2),
        "margin_rub": round(margin, 2),
        "buyout_pct": round(buyout, 1),
        "abc_group": group,
    }


def build_wb_finance_mini_app_extensions(
    revenue_total: float,
    wb_metrics: WbMarketplaceMetrics | None,
    *,
    matrix_rows: list[list[str]] | None,
    platform: str | None = None,
) -> dict[str, Any] | None:
    """Расширения table_raw_json для Mini App (ABC, SKU, summary)."""
    from services.marketplace_platform import normalize_marketplace_platform

    platform_id = normalize_marketplace_platform(platform)
    prompt_metrics = compute_wb_finance_prompt_metrics(
        revenue_total, wb_metrics, matrix_rows=matrix_rows, platform=platform_id
    )
    if prompt_metrics is None:
        return None

    from services.file_processor import compute_seller_matrix_etl

    matrix_etl = (
        compute_seller_matrix_etl(
            matrix_rows,
            revenue_total=revenue_total,
            platform=platform_id,
        )
        if matrix_rows
        else None
    )

    group_a: list[dict[str, Any]] = []
    group_b: list[dict[str, Any]] = []
    group_c: list[dict[str, Any]] = []
    if matrix_etl and matrix_etl.sku_catalog:
        for item in matrix_etl.sku_catalog:
            g = (item.abc_group or "B").upper()
            row = _abc_sku_to_dict(item, g)
            if g == "A":
                group_a.append(row)
            elif g == "C":
                group_c.append(row)
            else:
                group_b.append(row)
    elif prompt_metrics.sku_catalog_items:
        for item in prompt_metrics.sku_catalog_items:
            g = str(item.get("abc_group") or "B").upper()
            row = {
                "sku": str(item.get("article_id") or item.get("name") or "—"),
                "name": str(item.get("name") or "—"),
                "article_id": str(item.get("article_id") or "—"),
                "revenue": round(float(item.get("revenue_rub") or 0), 2),
                "revenue_rub": round(float(item.get("revenue_rub") or 0), 2),
                "margin": round(float(item.get("margin_rub") or 0), 2),
                "margin_rub": round(float(item.get("margin_rub") or 0), 2),
                "buyout_pct": round(float(item.get("buyout_pct") or 0), 1),
                "abc_group": g,
            }
            if g == "A":
                group_a.append(row)
            elif g == "C":
                group_c.append(row)
            else:
                group_b.append(row)

    oos_forecast: list[dict[str, Any]] = []
    if matrix_etl:
        for oos in matrix_etl.oos_forecasts:
            fomo = 0.0
            if oos.risk_out_of_stock and oos.days_until_stockout is not None:
                shortage = max(0.0, 7.0 - oos.days_until_stockout)
                fomo = max(0.0, shortage * 500.0)
            oos_forecast.append({
                "sku": oos.label,
                "name": oos.label,
                "days_until_stockout": (
                    round(oos.days_until_stockout, 1)
                    if oos.days_until_stockout is not None
                    else None
                ),
                "risk_out_of_stock": oos.risk_out_of_stock,
                "fomo_lost_rub": round(fomo, 2),
            })

    sku_catalog = list(prompt_metrics.sku_catalog_items)
    return {
        "source": "wb_ozon_finance_xlsx",
        "platform": platform_id,
        "abc_analysis": {
            "group_a": group_a,
            "group_b": group_b,
            "group_c": group_c,
        },
        "sku_catalog": sku_catalog,
        "out_of_stock_forecast": oos_forecast,
        "summary": {
            "revenue": round(prompt_metrics.revenue, 2),
            "revenue_rub": round(prompt_metrics.revenue, 2),
            "net_profit": round(prompt_metrics.clear_profit, 2),
            "business_score": prompt_metrics.business_score,
            "profitability_pct": round(prompt_metrics.profitability_pct, 1),
            "ad_load_pct": round(prompt_metrics.adv_load_pct, 1),
            "buyout_coef_pct": round(prompt_metrics.buy_ratio_pct, 1),
            "fomo_rub": round(prompt_metrics.fomo_lost_rub, 2),
            "group_a_leader": prompt_metrics.abc_a_leader_name,
        },
    }


def enrich_table_json_wb_finance(
    table_json: str,
    *,
    revenue_total: float,
    wb_metrics: WbMarketplaceMetrics | None = None,
    matrix_rows: list[list[str]] | None = None,
    platform: str | None = None,
) -> str:
    """Дополняет канонический JSON отчёта полями для Mini App дашборда."""
    from services.table_json import canonicalize_table_json

    extensions = build_wb_finance_mini_app_extensions(
        revenue_total,
        wb_metrics,
        matrix_rows=matrix_rows,
        platform=platform,
    )
    if not extensions:
        return table_json
    try:
        payload = json.loads(table_json)
    except json.JSONDecodeError:
        return table_json
    if not isinstance(payload, dict):
        return table_json
    payload.update(extensions)
    return canonicalize_table_json(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def append_wb_finance_mini_app_cta(html: str) -> str:
    """Раньше добавлял CTA в чат; Studio открывается через MenuButtonWebApp."""
    return repair_telegram_html((html or "").strip())


def _format_abc_a_leader_html(prompt_metrics: WbFinancePromptMetrics) -> str:
    """Лидер A: реальный SKU или сообщение о санации матрицы."""
    if prompt_metrics.revenue > 0 and (
        prompt_metrics.abc_a_leader_name in ("—", "")
        or (
            prompt_metrics.abc_a_leader_margin <= 0
            and prompt_metrics.abc_a_leader_buyout <= 0
            and prompt_metrics.abc_a_count == 0
        )
    ):
        return (
            "🅰️ <b>Товары-лидеры (Приносят основные деньги группы А):</b> "
            "<i>Отсутствуют, выручка требует оптимизации</i>"
        )
    label = _escape_verdict(
        _format_sku_label(
            prompt_metrics.abc_a_leader_name,
            prompt_metrics.abc_a_leader_article,
        )
    )
    return (
        f"🅰️ <b>Товары-лидеры (Приносят основные деньги группы А):</b> <b>{label}</b> "
        f"(выкуп: <code>{prompt_metrics.abc_a_leader_buyout:.1f}%</code>)"
    )


def build_wb_finance_express_html_local(
    prompt_metrics: WbFinancePromptMetrics,
    wb_metrics: WbMarketplaceMetrics | None,
) -> str:
    """Премиальный локальный отчёт (0 ₽) — тот же стиль, что и консалтинг CFO."""
    score_emoji, score_status = _business_score_band(prompt_metrics.business_score)
    score_reason = _business_score_reason_line(prompt_metrics, wb_metrics)
    lines = [
        "📊 <b>ФИНАНСОВЫЙ ЭКСПРЕСС-АНАЛИЗ МАГАЗИНА</b>",
        _FINANCE_SEPARATOR,
        (
            f"🎯 <b>ИНДЕКС ЗДОРОВЬЯ БИЗНЕСА:</b> {score_emoji} "
            f"<code>{prompt_metrics.business_score:.1f} / 10</code> "
            f"<i>{score_status}</i>"
        ),
        score_reason,
        "💡 <b>ГЛАВНЫЙ АНАЛИТИЧЕСКИЙ ВЫВОД:</b>",
        f"<i>{_escape_verdict(prompt_metrics.verdict)}</i>",
        _FINANCE_SEPARATOR,
        f"💰 <b>ОБЩАЯ ВЫРУЧКА:</b> <code>{_fmt_rub_in_code(prompt_metrics.revenue)} руб.</code>",
        f"📉 <b>НАЛОГ УСН (6%):</b> <code>{_fmt_rub_in_code(prompt_metrics.tax)} руб.</code>",
        (
            f"💵 <b>ЧИСТАЯ ПРИБЫЛЬ:</b> "
            f"<code>{_fmt_rub_in_code(prompt_metrics.clear_profit)} руб.</code>"
        ),
        (
            f"Эффективность (рентабельность) чистой прибыли: "
            f"<code>{prompt_metrics.profitability_pct:.1f}%</code>"
        ),
        _FINANCE_SEPARATOR,
        "📦 <b>ABC-АНАЛИЗ ПРОДАЖ</b>",
        _format_abc_a_leader_html(prompt_metrics),
        "🅲 <b>Товары-аутсайдеры (Слабые продажи группы С):</b>",
    ]
    for c_line in prompt_metrics.abc_c_summary.splitlines():
        if c_line.strip():
            lines.append(_escape_verdict(c_line))
    lines.append("📦 <b>Проблемные зоны и скрытые убытки матрицы:</b>")
    for zone_line in prompt_metrics.matrix_problem_zones_block.splitlines():
        if zone_line.strip():
            lines.append(_escape_verdict(zone_line))
    lines.extend(
        [
        _FINANCE_SEPARATOR,
        "📈 <b>СВЕТОФОР ЭФФЕКТИВНОСТИ</b>",
    ]
    )
    for zone_line in _build_traffic_light_block(wb_metrics, prompt_metrics):
        lines.append(zone_line)
    lines.extend(
        [
            _FINANCE_SEPARATOR,
            "💸 <b>КАЛЬКУЛЯТОР ПОТЕРЬ И УПУЩЕННОЙ ВЫГОДЫ</b>",
            (
                f"Потенциально можно вернуть в оборот: "
                f"<code>{_fmt_rub_in_code(prompt_metrics.fomo_lost_rub)} руб.</code>"
            ),
        ]
    )
    fomo_items = _expand_fomo_breakdown(prompt_metrics.fomo_breakdown)
    if fomo_items:
        for part in fomo_items:
            lines.append(f"• {_escape_verdict(part)}")
    else:
        lines.append("• критических зон упущенной выгоды не выявлено")
    lines.append(
        f"<i>Исправление выявленных зон вернёт в оборот до "
        f"<code>{_fmt_rub_in_code(prompt_metrics.fomo_lost_rub)} руб.</code></i>"
    )
    lines.extend(
        [
            _FINANCE_SEPARATOR,
            "🛡️ <b>ПРОГНОЗ И ОБНУЛЕНИЕ ОСТАТКОВ</b>",
            (
                f"<i>При сохранении текущего темпа годовой оборот составит около "
                f"<code>{_fmt_rub_in_code(prompt_metrics.year_forecast, decimals=0)} руб.</code>.</i>"
            ),
            (
                f"⚠️ <b>Заканчивается товар:</b> "
                f"<i>{_escape_verdict(_dedupe_report_noise(prompt_metrics.oos_forecast_line))}</i>"
            ),
            _FINANCE_SEPARATOR,
            "📋 <b>ПЛАН ДЕЙСТВИЙ ДЛЯ ПРЕДПРИНИМАТЕЛЯ НА СЕГОДНЯ</b>",
        ]
    )
    lines.extend(_build_strategic_plan_lines(prompt_metrics, wb_metrics))
    lines.append(f"<i>CFO build {_FINANCE_REPORT_BUILD}</i>")
    return append_wb_finance_mini_app_cta("\n".join(lines))


def _escape_verdict(text: str) -> str:
    from services.telegram_safe_text import _escape_telegram_html

    return _escape_telegram_html(text)


def _build_traffic_light_block(
    wb_metrics: WbMarketplaceMetrics | None,
    prompt_metrics: WbFinancePromptMetrics,
) -> list[str]:
    """🟢🟡🔴 блоки для локального fallback."""
    leader_label = _escape_verdict(
        _format_sku_label(
            prompt_metrics.abc_a_leader_name,
            prompt_metrics.abc_a_leader_article,
        )
    )
    leader_buyout = prompt_metrics.abc_a_leader_buyout
    leader_margin = prompt_metrics.abc_a_leader_margin
    leader_ok = _leader_buyout_is_healthy(leader_buyout, leader_margin)

    green = "🟢 <b>ЗОНА УСПЕХА:</b> "
    if not leader_ok:
        if wb_metrics and wb_metrics.top5_units:
            healthy = [u for u in wb_metrics.top5_units if u.net_income > 0]
            if healthy:
                best = max(healthy, key=lambda u: u.net_income)
                green += (
                    f"Масштабируйте <b>{_escape_verdict(best.label)}</b> "
                    f"(<code>{_fmt_rub_in_code(best.net_income)}</code>/шт.) — "
                    "здоровый драйвер маржи. "
                    f"<b>{leader_label}</b> в критической зоне, не масштабировать."
                )
            else:
                green += "сначала восстановите выкуп и маржу — масштабировать пока нечего."
        else:
            green += (
                "сначала поднимите выкуп по карточкам — масштабирование отложите."
            )
    else:
        green += (
            f"Товары-лидеры группы A — <b>{leader_label}</b>, "
            f"маржа <code>{_fmt_rub_in_code(leader_margin)}</code> руб., "
            f"выкуп <code>{leader_buyout:.1f}%</code>. "
            "Масштабируйте закуп и рекламу на этот артикул."
        )

    yellow = "🟡 <b>ЗОНА ВНИМАНИЯ:</b> "
    yellow_parts: list[str] = []
    if prompt_metrics.adv_load_pct >= _DRR_WARNING_PCT:
        ad_cost = prompt_metrics.total_ad_cost
        loss_hint = (
            f" — потери на рекламу <code>{_fmt_rub_in_code(ad_cost)}</code> руб."
            if ad_cost > 0
            else ""
        )
        yellow_parts.append(
            f"ДРР <code>{prompt_metrics.adv_load_pct:.1f}%</code> выше 20%: "
            f"работаете на рекламу, а не на карман{loss_hint}."
        )
    loc_line = (prompt_metrics.localization_index_line or "").lower()
    if any(
        token in loc_line
        for token in ("низк", "ниже", "плох", "критич", "0.", "1.", "2.", "3.")
    ) and "не указан" not in loc_line:
        yellow_parts.append(
            "низкий индекс локализации — распределите остатки на Казань, "
            "Краснодар и Электросталь, чтобы снизить логистику WB."
        )
    elif wb_metrics and 45 <= wb_metrics.buyout_coef_pct < 65:
        yellow_parts.append(
            f"выкуп <code>{wb_metrics.buyout_coef_pct:.1f}%</code> — подтяните инфографику, "
            "отзывы и размерную сетку."
        )
    elif wb_metrics and prompt_metrics.adv_load_pct > _HIGH_DRR_PCT:
        yellow_parts.append(
            f"ДРР <code>{prompt_metrics.adv_load_pct:.1f}%</code> выше нормы — "
            "сузьте ключевые слова и отключите слабые кампании."
        )
    elif wb_metrics and 12 < wb_metrics.ad_load_pct <= _HIGH_DRR_PCT:
        yellow_parts.append(
            f"реклама <code>{wb_metrics.ad_load_pct:.1f}%</code> — еженедельно отключайте "
            "кампании с ДРР выше целевого."
        )
    if not yellow_parts:
        yellow_parts.append(
            "контролируйте оборачиваемость и не раздувайте склад неликвидом без спроса."
        )
    yellow += " ".join(yellow_parts)

    red = "🔴 <b>КРИТИЧЕСКАЯ ЗОНА:</b> "
    red_parts: list[str] = []
    if not leader_ok:
        if leader_buyout < _CRITICAL_BUYOUT_PCT:
            red_parts.append(
                f"<b>{leader_label}</b> — "
                f"главный источник убытков: выкуп <code>{leader_buyout:.1f}%</code>, "
                "масштабирование запрещено."
            )
        else:
            red_parts.append(
                f"<b>{leader_label}</b> — выкуп "
                f"<code>{leader_buyout:.1f}%</code>: нельзя масштабировать, "
                "сначала карточка и логистика."
            )
    if prompt_metrics.outsider_name not in ("—", "") and prompt_metrics.outsider_loss > 0:
        outsider_label = _escape_verdict(
            _format_sku_label(
                prompt_metrics.outsider_name,
                prompt_metrics.outsider_article,
            )
        )
        if outsider_label.lower() != leader_label.lower():
            red_parts.append(
                f"Аутсайдер <b>{outsider_label}</b> — убыток "
                f"<code>{_fmt_rub_in_code(prompt_metrics.outsider_loss)}</code> руб., "
                f"выкуп <code>{prompt_metrics.outsider_buyout:.1f}%</code>."
            )
    elif wb_metrics and wb_metrics.top5_units:
        worst = min(wb_metrics.top5_units, key=lambda u: u.net_income)
        if worst.net_income < 0:
            red_parts.append(
                f"«{worst.label}» убыточен "
                f"(<code>{_fmt_rub_in_code(worst.net_income)}</code>/шт.)."
            )
    if prompt_metrics.adv_load_pct >= 30:
        red_parts.append(
            f"Катастрофический ДРР <code>{prompt_metrics.adv_load_pct:.1f}%</code> — "
            "реклама съедает чистую прибыль быстрее, чем растёт выручка."
        )
    elif prompt_metrics.adv_load_pct > _HIGH_DRR_PCT:
        red_parts.append(
            f"ДРР <code>{prompt_metrics.adv_load_pct:.1f}%</code> — срочно режьте рекламный бюджет."
        )
    if not red_parts:
        red += "критических утечек не зафиксировано — держите фокус на жёлтой зоне."
    else:
        red += " ".join(red_parts)

    return [green, "", yellow, "", red]


async def generate_wb_finance_consulting_html(
    settings: Settings,
    *,
    revenue_total: float,
    wb_metrics: WbMarketplaceMetrics | None = None,
    matrix_rows: list[list[str]] | None = None,
    models: list[str] | None = None,
    http_client: object | None = None,
    platform: str | None = None,
    file_path: str | Path | None = None,
    wb_json_str: str | None = None,
) -> str | None:
    """
    CFO-отчёт wb_ozon_finance: локальный HTML (мгновенно); OpenRouter — только по флагу.
    """
    import asyncio

    from content.chat_prompt import WB_ANALYTICS_SYSTEM_PROMPT

    revenue_total = float(revenue_total or 0.0)
    if revenue_total <= 0:
        revenue_total = await asyncio.to_thread(
            resolve_wb_revenue_total,
            calculated_total=0.0,
            file_path=file_path,
            matrix_rows=matrix_rows,
            platform=platform,
        )
    if revenue_total <= 0:
        return None

    if wb_metrics is None and matrix_rows:
        wb_metrics = resolve_wb_metrics_for_rows(
            matrix_rows, revenue_total, platform=platform
        )

    local_html = await asyncio.to_thread(
        _build_local_wb_finance_html,
        revenue_total,
        wb_metrics,
        matrix_rows=matrix_rows,
        platform=platform,
    )

    use_openrouter = bool(
        getattr(settings, "wb_finance_openrouter_html", False)
        and settings.openrouter_key
    )
    if not use_openrouter:
        return local_html

    ctx: dict[str, Any] | None = None
    if wb_json_str:
        try:
            parsed = json.loads(wb_json_str)
            if isinstance(parsed, dict) and not parsed.get("error"):
                ctx = parsed
        except json.JSONDecodeError:
            ctx = None
    if ctx is None:
        ctx = await asyncio.to_thread(
            resolve_wb_mpstats_context,
            file_path=file_path,
            matrix_rows=matrix_rows,
            revenue_total=revenue_total,
            platform=platform,
        )
    if not ctx or ctx.get("error"):
        return local_html

    model_chain: list[str] = []
    if models:
        model_chain.extend(m for m in models if m and m not in model_chain)
    for mid in settings.free_models:
        if mid and mid not in model_chain:
            model_chain.append(mid)

    messages = [
        {"role": "system", "content": WB_ANALYTICS_SYSTEM_PROMPT},
        {"role": "user", "content": build_wb_finance_json_user_message(ctx)},
    ]
    try:
        completion = await ask_ai_messages(
            settings,
            messages,
            timeout=min(12.0, settings.openrouter_timeout_sec),
            max_context_tokens=settings.chat_max_context_tokens_est,
            char_per_token=settings.chat_char_per_token_est,
            http_client=http_client,
            models=model_chain or None,
            max_tokens=settings.openrouter_premium_max_output_tokens,
            text_role="table_generator",
        )
        raw = sanitize_wb_finance_html(completion.get("content") or "")
        if _is_publishable_wb_finance_html(raw):
            if "cfo-v8" not in raw.lower():
                raw = f"{raw}\n\n<i>CFO build {_FINANCE_REPORT_BUILD}</i>"
            return append_wb_finance_mini_app_cta(raw)
        logger.warning(
            "generate_wb_finance_consulting_html: OpenRouter HTML rejected, using local fallback"
        )
    except Exception:
        logger.exception("generate_wb_finance_consulting_html: OpenRouter failed")

    return local_html


def resolve_wb_metrics_for_rows(
    rows: list[list[str]],
    revenue_total: float,
    *,
    platform: str | None = None,
) -> WbMarketplaceMetrics | None:
    """Локальные метрики маркетплейса для промпта и fallback."""
    return compute_wb_marketplace_metrics(
        rows, revenue_total=revenue_total, platform=platform
    )
