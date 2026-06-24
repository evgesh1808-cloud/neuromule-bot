"""ИИ-консалтинг для под-режима wb_ozon_finance (метрики ETL → OpenRouter)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

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
_WB_FINANCE_AI_TEMPERATURE = 0.45
_WB_FINANCE_MAX_OUTPUT_TOKENS = 2800


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
    total_ad_cost: float = 0.0
    sales_qty: float = 0.0
    returns_qty: float = 0.0
    deliveries_qty: float = 0.0


def compute_wb_finance_prompt_metrics(
    revenue_total: float,
    wb_metrics: WbMarketplaceMetrics | None = None,
) -> WbFinancePromptMetrics | None:
    """Собирает переменные {revenue}, {tax}, {clear_profit}, {adv_load}, {buy_ratio}, {year_forecast}."""
    if revenue_total <= 0:
        return None
    tax = revenue_total * _USN_RATE
    clear_profit = revenue_total - tax
    profitability = (clear_profit / revenue_total * 100.0) if revenue_total > 0 else 0.0
    adv_load = wb_metrics.ad_load_pct if wb_metrics else 0.0
    buy_ratio = wb_metrics.buyout_coef_pct if wb_metrics else 0.0
    return WbFinancePromptMetrics(
        revenue=revenue_total,
        tax=tax,
        clear_profit=clear_profit,
        adv_load_pct=adv_load,
        buy_ratio_pct=buy_ratio,
        year_forecast=revenue_total * 12,
        profitability_pct=profitability,
        total_ad_cost=wb_metrics.total_advertising_cost if wb_metrics else 0.0,
        sales_qty=wb_metrics.sales_qty if wb_metrics else 0.0,
        returns_qty=wb_metrics.returns_qty if wb_metrics else 0.0,
        deliveries_qty=wb_metrics.deliveries_qty if wb_metrics else 0.0,
    )


def build_wb_marketplace_finance_user_prompt(
    prompt_metrics: WbFinancePromptMetrics,
    wb_metrics: WbMarketplaceMetrics | None,
) -> str:
    """User-сообщение: расширенный JSON с ETL-метриками для качественного анализа."""
    top5 = []
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
    payload: dict[str, Any] = {
        "etl_source": "local_python_wb_ozon_finance",
        "revenue_rub": round(prompt_metrics.revenue, 2),
        "tax_usn_6pct_rub": round(prompt_metrics.tax, 2),
        "clear_profit_rub": round(prompt_metrics.clear_profit, 2),
        "profitability_pct": round(prompt_metrics.profitability_pct, 1),
        "ad_load_pct": round(prompt_metrics.adv_load_pct, 1),
        "buyout_coef_pct": round(prompt_metrics.buy_ratio_pct, 1),
        "year_forecast_rub": round(prompt_metrics.year_forecast, 0),
        "total_advertising_cost_rub": round(prompt_metrics.total_ad_cost, 2),
        "sales_qty": prompt_metrics.sales_qty,
        "returns_qty": prompt_metrics.returns_qty,
        "deliveries_qty": prompt_metrics.deliveries_qty,
        "top5_unit_economics": top5,
        "local_insights": list(wb_metrics.insight_lines) if wb_metrics else [],
    }
    return (
        "Ниже — подтверждённые метрики локального ETL-расчёта NeuroMule. "
        "Сформируй финансовый экспресс-отчёт строго по структуре из system prompt. "
        "Числа в блоке выручки/налога/прибыли не меняй.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def build_wb_finance_system_prompt_from_totals(
    revenue_total: float,
    wb_metrics: WbMarketplaceMetrics | None = None,
) -> str | None:
    """Удобная обёртка: revenue + wb_metrics → готовый system prompt."""
    metrics = compute_wb_finance_prompt_metrics(revenue_total, wb_metrics)
    if metrics is None:
        return None
    return build_wb_marketplace_finance_system_prompt(
        revenue=_fmt_rub_in_code(metrics.revenue),
        tax=_fmt_rub_in_code(metrics.tax),
        clear_profit=_fmt_rub_in_code(metrics.clear_profit),
        adv_load=f"{metrics.adv_load_pct:.1f}",
        buy_ratio=f"{metrics.buy_ratio_pct:.1f}",
        year_forecast=_fmt_rub_in_code(metrics.year_forecast, decimals=0),
    )


async def generate_wb_finance_consulting_html(
    settings: Settings,
    *,
    revenue_total: float,
    wb_metrics: WbMarketplaceMetrics | None = None,
    models: list[str] | None = None,
    http_client: object | None = None,
) -> str | None:
    """
    Генерирует HTML-консалтинг через OpenRouter.

    При ошибке возвращает ``None`` — вызывающий код использует локальный fallback.
    """
    prompt_metrics = compute_wb_finance_prompt_metrics(revenue_total, wb_metrics)
    if prompt_metrics is None:
        return None

    system = build_wb_marketplace_finance_system_prompt(
        revenue=_fmt_rub_in_code(prompt_metrics.revenue),
        tax=_fmt_rub_in_code(prompt_metrics.tax),
        clear_profit=_fmt_rub_in_code(prompt_metrics.clear_profit),
        adv_load=f"{prompt_metrics.adv_load_pct:.1f}",
        buy_ratio=f"{prompt_metrics.buy_ratio_pct:.1f}",
        year_forecast=_fmt_rub_in_code(prompt_metrics.year_forecast, decimals=0),
    )
    user = build_wb_marketplace_finance_user_prompt(prompt_metrics, wb_metrics)

    model_chain = [m for m in (models or settings.free_models) if str(m).strip()]
    if not model_chain:
        return None

    try:
        completion = await ask_ai_messages(
            settings,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            timeout=settings.openrouter_timeout_sec,
            http_client=http_client,
            models=model_chain,
            max_tokens=_WB_FINANCE_MAX_OUTPUT_TOKENS,
            temperature=_WB_FINANCE_AI_TEMPERATURE,
        )
    except Exception:
        logger.exception("wb_finance AI consulting request failed")
        return None

    content = (completion.get("content") or "").strip()
    if not content:
        return None
    return repair_telegram_html(content)


def resolve_wb_metrics_for_rows(
    rows: list[list[str]],
    revenue_total: float,
) -> WbMarketplaceMetrics | None:
    """Локальные метрики WB/Ozon для промпта и fallback."""
    return compute_wb_marketplace_metrics(rows, revenue_total=revenue_total)
