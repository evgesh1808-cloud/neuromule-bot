"""ИИ-консалтинг для под-режима wb_ozon_finance (метрики ETL → OpenRouter)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

from config import Settings
from content.chat_prompt import build_wb_marketplace_finance_system_prompt
from content.messages import TXT_WB_FINANCE_MINI_APP_CTA
from services.ai_text import ask_ai_messages
from services.table_text_response import (
    WbMarketplaceMetrics,
    _fmt_rub_in_code,
    compute_wb_marketplace_metrics,
)
from services.telegram_safe_text import repair_telegram_html

logger = logging.getLogger(__name__)

_USN_RATE = 0.06
_LOW_BUYOUT_PCT = 40.0
_HIGH_DRR_PCT = 18.0
_CRITICAL_BUYOUT_PCT = 5.0


def _format_sku_bullet_lines(
    items: Iterable[tuple[str, str]],
    *,
    max_items: int = 5,
    overflow_suffix: str | None = None,
) -> str:
    """Список SKU маркерами «•», по одному на строку."""
    lines: list[str] = []
    batch = list(items)
    for name, article in batch[:max_items]:
        lines.append(f"• {name} (Арт: {article})")
    if overflow_suffix:
        lines.append(overflow_suffix)
    elif len(batch) > max_items:
        lines.append(f"• … и ещё {len(batch) - max_items} SKU")
    return "\n".join(lines) if lines else "• убыточных SKU не выявлено"


def _expand_fomo_breakdown(parts: tuple[str, ...]) -> tuple[str, ...]:
    """Разбивает строки с «;» на отдельные пункты для маркированного списка."""
    expanded: list[str] = []
    for part in parts:
        chunk = (part or "").strip()
        if not chunk:
            continue
        if chunk.startswith("Логистика невыкупленных:"):
            chunk = chunk.removeprefix("Логистика невыкупленных:").strip()
        if "; " in chunk:
            expanded.extend(p.strip() for p in chunk.split("; ") if p.strip())
        else:
            expanded.append(chunk)
    return tuple(expanded)


def _leader_buyout_is_healthy(buyout_pct: float) -> bool:
    return buyout_pct >= _LOW_BUYOUT_PCT


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
    leader_name = _escape_verdict(prompt_metrics.abc_a_leader_name)
    leader_art = _escape_verdict(prompt_metrics.abc_a_leader_article)
    lines: list[str] = []

    if _leader_buyout_is_healthy(prompt_metrics.abc_a_leader_buyout):
        lines.append(
            f"<b>1.</b> Усилить закуп и рекламу на <b>{leader_name}</b> "
            f"(арт. <code>{leader_art}</code>) — лидер A с выкупом "
            f"<code>{prompt_metrics.abc_a_leader_buyout:.1f}%</code>."
        )
    elif wb_metrics and wb_metrics.top5_units:
        scale_candidate = None
        for unit in sorted(wb_metrics.top5_units, key=lambda u: u.net_income, reverse=True):
            if unit.net_income > 0:
                scale_candidate = unit
                break
        if scale_candidate:
            lines.append(
                f"<b>1.</b> Масштабируйте <b>{_escape_verdict(scale_candidate.label)}</b> "
                f"(маржа <code>{_fmt_rub_in_code(scale_candidate.net_income)}</code>/шт.) — "
                f"лидер A с выкупом <code>{prompt_metrics.abc_a_leader_buyout:.1f}%</code> "
                "в критической зоне."
            )
        else:
            lines.append(
                "<b>1.</b> Не масштабируйте SKU с нулевым выкупом — сначала поднимите конверсию карточки."
            )
    else:
        lines.append(
            f"<b>1.</b> Не масштабируйте <b>{leader_name}</b> (арт. <code>{leader_art}</code>): "
            f"выкуп <code>{prompt_metrics.abc_a_leader_buyout:.1f}%</code> — сначала карточка и логистика."
        )

    if prompt_metrics.adv_load_pct > _HIGH_DRR_PCT:
        lines.append(
            f"<b>2.</b> Срочно <b>снизить ДРР</b> с <code>{prompt_metrics.adv_load_pct:.1f}%</code> "
            "до 12–15%: отключите неокупаемые кампании, сузьте ключевые слова и ставки."
        )
    elif prompt_metrics.outsider_name not in ("—", "") and prompt_metrics.outsider_loss > 0:
        out_name = _escape_verdict(prompt_metrics.outsider_name)
        out_art = _escape_verdict(prompt_metrics.outsider_article)
        lines.append(
            f"<b>2.</b> Для <b>{out_name}</b> (арт. <code>{out_art}</code>) поднимите цену "
            f"или остановите рекламу — убыток "
            f"<code>{_fmt_rub_in_code(prompt_metrics.outsider_loss)}</code> руб."
        )
    else:
        lines.append(
            f"<b>2.</b> Держите ДРР не выше <code>15%</code> — еженедельно чистите неокупаемые ключи."
        )

    lines.append(
        f"<b>3.</b> Контролируйте остатки по рисковым SKU: "
        f"<i>{_escape_verdict(prompt_metrics.oos_forecast_line)}</i>"
    )
    return lines
_WB_FINANCE_MAX_OUTPUT_TOKENS = 1400
_WB_FINANCE_TELEGRAM_SOFT_MAX_CHARS = 2200
_FINANCE_SEPARATOR = "────────────────────────"
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
    (re.compile(r"ИИ[\s\-–—]*План\s*действий", re.IGNORECASE), "СТРАТЕГИЧЕСКИЙ ПЛАН ДЕЙСТВИЙ НА СЕГОДНЯ"),
    (re.compile(r"ИИ[\s\-–—]*Инсайт", re.IGNORECASE), "КЛЮЧЕВОЙ БИЗНЕС-ВЕРДИКТ"),
    (re.compile(r"Серверный\s+расч[её]т", re.IGNORECASE), "Потенциальная упущенная выгода"),
    (re.compile(r"ABC[\s\-–—]*АНАЛИЗ\s+МАТРИЦЫ\s*\(\s*локальный\s+ETL[^)]*\)", re.IGNORECASE), "ABC-АНАЛИЗ МАТРИЦЫ"),
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
        ret_rate = wb_metrics.returns_qty / wb_metrics.deliveries_qty
        if ret_rate > 0.12:
            penalty = revenue_total * ret_rate * 0.08
            if penalty > 30:
                total += penalty
                parts.append(
                    f"возвраты {wb_metrics.returns_qty:.0f} шт. → штрафы/обратная логистика ≈ "
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
) -> WbFinancePromptMetrics | None:
    """Собирает переменные ETL для system/user prompt и локального fallback."""
    if revenue_total <= 0:
        return None

    from services.file_processor import compute_seller_matrix_etl

    matrix_etl = (
        compute_seller_matrix_etl(matrix_rows, revenue_total=revenue_total)
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
    outsider_name = "—"
    outsider_article = "—"
    outsider_loss = 0.0
    outsider_buyout = 0.0
    sku_catalog_lines: tuple[str, ...] = ()
    sku_catalog_items: tuple[dict[str, Any], ...] = ()
    oos_line = "данных по остаткам недостаточно"
    if matrix_etl:
        logistics_fomo = matrix_etl.logistics_fomo_rub
        if matrix_etl.logistics_fomo_rub > 0:
            fomo_rub = round(fomo_rub + logistics_fomo, 2)
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
                max_items=5,
            )
        elif abc_c_count == 0:
            abc_c_summary = "убыточных SKU не выявлено"
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
        if matrix_etl.oos_critical_sku and matrix_etl.oos_critical_days is not None:
            oos_line = (
                f"«{matrix_etl.oos_critical_sku}» закончится через "
                f"{matrix_etl.oos_critical_days:.0f} дн. (риск OOS)"
            )
        elif matrix_etl.oos_forecasts:
            oos_line = "критических OOS по остаткам не выявлено"

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
        "outsider_name": metrics.outsider_name,
        "outsider_article": metrics.outsider_article,
        "outsider_loss": _fmt_rub_in_code(metrics.outsider_loss),
        "outsider_buyout": f"{metrics.outsider_buyout:.1f}",
        "abc_a_leader_buyout": f"{metrics.abc_a_leader_buyout:.1f}",
        "sku_catalog_block": catalog_block,
        "fomo_details_block": _format_fomo_details_block(metrics.fomo_breakdown),
        "oos_forecast_line": metrics.oos_forecast_line,
    }


def build_wb_marketplace_finance_user_prompt(
    prompt_metrics: WbFinancePromptMetrics,
    wb_metrics: WbMarketplaceMetrics | None,
) -> str:
    """User-сообщение: расширенный JSON с ETL-метриками для качественного анализа."""
    top5 = []
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

    payload: dict[str, Any] = {
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
    return (
        "Ниже — подтверждённые финансовые показатели и каталог SKU из отчёта маркетплейса. "
        "Сформируй экспресс-отчёт строго по структуре из system prompt. "
        "Числа выручки, налога, прибыли, рентабельности, скоринга, упущенной выгоды, ABC и OOS не меняй. "
        "Используй точные названия товаров и артикулы из sku_catalog — без обобщений. "
        "Пиши как финансовый директор, без слов «ИИ», «серверный», «алгоритм». "
        f"Весь ответ до {_WB_FINANCE_TELEGRAM_SOFT_MAX_CHARS} символов.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


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
) -> tuple[str, str] | None:
    """Пара system + user для OpenRouter после локального ETL матрицы."""
    if wb_metrics is None:
        wb_metrics = resolve_wb_metrics_for_rows(matrix_rows, revenue_total)
    prompt_metrics = compute_wb_finance_prompt_metrics(
        revenue_total, wb_metrics, matrix_rows=matrix_rows
    )
    if prompt_metrics is None:
        return None
    system = build_wb_marketplace_finance_system_prompt(**_prompt_kwargs_from_metrics(prompt_metrics))
    user = build_wb_marketplace_finance_user_prompt(prompt_metrics, wb_metrics)
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
    return text


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
) -> dict[str, Any] | None:
    """Расширения table_raw_json для Mini App (ABC, SKU, summary)."""
    prompt_metrics = compute_wb_finance_prompt_metrics(
        revenue_total, wb_metrics, matrix_rows=matrix_rows
    )
    if prompt_metrics is None:
        return None

    from services.file_processor import compute_seller_matrix_etl

    matrix_etl = (
        compute_seller_matrix_etl(matrix_rows, revenue_total=revenue_total)
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
) -> str:
    """Дополняет канонический JSON отчёта полями для Mini App дашборда."""
    from services.table_json import canonicalize_table_json

    extensions = build_wb_finance_mini_app_extensions(
        revenue_total, wb_metrics, matrix_rows=matrix_rows
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
    """Добавляет единый CTA про Автопилот API и Mini App (если ещё нет)."""
    text = (html or "").strip()
    if any(marker in text for marker in _OLD_FINALE_MARKERS):
        text = re.sub(
            r"\n*────────────────────────\n*🗂️.*\Z",
            "",
            text,
            flags=re.DOTALL,
        ).strip()
    if "Автопилот по API" in text:
        return repair_telegram_html(text)
    block = f"{_FINANCE_SEPARATOR}\n{TXT_WB_FINANCE_MINI_APP_CTA}"
    return repair_telegram_html(f"{text}\n\n{block}" if text else block)


def build_wb_finance_express_html_local(
    prompt_metrics: WbFinancePromptMetrics,
    wb_metrics: WbMarketplaceMetrics | None,
) -> str:
    """Премиальный локальный отчёт (0 ₽) — тот же стиль, что и консалтинг CFO."""
    leader_name = _escape_verdict(prompt_metrics.abc_a_leader_name)
    leader_art = _escape_verdict(prompt_metrics.abc_a_leader_article)
    lines = [
        "📊 <b>ФИНАНСОВЫЙ ЭКСПРЕСС-АНАЛИЗ БИЗНЕСА</b>",
        _FINANCE_SEPARATOR,
        (
            f"🎯 <b>БИЗНЕС-СКОРИНГ МАГАЗИНА:</b> "
            f"<code>{prompt_metrics.business_score:.1f} / 10</code>"
        ),
        "💡 <b>КЛЮЧЕВОЙ БИЗНЕС-ВЕРДИКТ:</b>",
        f"<i>{_escape_verdict(prompt_metrics.verdict)}</i>",
        _FINANCE_SEPARATOR,
        f"💰 <b>ВАЛОВАЯ ВЫРУЧКА:</b> <code>{_fmt_rub_in_code(prompt_metrics.revenue)} руб.</code>",
        f"📉 <b>НАЛОГ УСН (6%):</b> <code>{_fmt_rub_in_code(prompt_metrics.tax)} руб.</code>",
        (
            f"💵 <b>ЧИСТАЯ ПРИБЫЛЬ:</b> "
            f"<code>{_fmt_rub_in_code(prompt_metrics.clear_profit)} руб.</code>"
        ),
        (
            f"Рентабельность по чистой прибыли: "
            f"<code>{prompt_metrics.profitability_pct:.1f}%</code>"
        ),
        _FINANCE_SEPARATOR,
        "📦 <b>ABC-АНАЛИЗ МАТРИЦЫ</b>",
        (
            f"🅰️ Лидер группы A: <b>{_escape_verdict(prompt_metrics.abc_a_leader_name)}</b> "
            f"(Артикул: <code>{_escape_verdict(prompt_metrics.abc_a_leader_article)}</code>)"
        ),
        "🅲 <b>Группа C:</b>",
    ]
    for c_line in prompt_metrics.abc_c_summary.splitlines():
        lines.append(_escape_verdict(c_line))
    lines.extend(
        [
        _FINANCE_SEPARATOR,
        "📈 <b>СВЕТОФОР ЗДОРОВЬЯ БИЗНЕСА</b>",
    ]
    )
    for zone_line in _build_traffic_light_block(wb_metrics, prompt_metrics):
        lines.append(zone_line)
    lines.extend(
        [
            _FINANCE_SEPARATOR,
            "💸 <b>КАЛЬКУЛЯТОР УПУЩЕННОЙ ВЫГОДЫ</b>",
            (
                f"Потенциальная упущенная выгода: "
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
            "🛡️ <b>ПРОГНОЗ И КРЭШ-ТЕСТ</b>",
            (
                f"<i>При сохранении динамики годовой оборот — около "
                f"<code>{_fmt_rub_in_code(prompt_metrics.year_forecast, decimals=0)} руб.</code>. "
                f"ДРР {prompt_metrics.adv_load_pct:.1f}%, рентабельность "
                f"{prompt_metrics.profitability_pct:.1f}%.</i>"
            ),
            f"📦 <b>OOS:</b> <i>{_escape_verdict(prompt_metrics.oos_forecast_line)}</i>",
            _FINANCE_SEPARATOR,
            "📋 <b>СТРАТЕГИЧЕСКИЙ ПЛАН ДЕЙСТВИЙ НА СЕГОДНЯ</b>",
        ]
    )
    lines.extend(_build_strategic_plan_lines(prompt_metrics, wb_metrics))
    return append_wb_finance_mini_app_cta("\n".join(lines))


def _escape_verdict(text: str) -> str:
    from services.telegram_safe_text import _escape_telegram_html

    return _escape_telegram_html(text)


def _build_traffic_light_block(
    wb_metrics: WbMarketplaceMetrics | None,
    prompt_metrics: WbFinancePromptMetrics,
) -> list[str]:
    """🟢🟡🔴 блоки для локального fallback."""
    leader_name = _escape_verdict(prompt_metrics.abc_a_leader_name)
    leader_art = _escape_verdict(prompt_metrics.abc_a_leader_article)
    leader_buyout = prompt_metrics.abc_a_leader_buyout
    leader_is_critical = not _leader_buyout_is_healthy(leader_buyout)

    green = "🟢 <b>ЗОНА УСПЕХА:</b> "
    if leader_is_critical:
        if wb_metrics and wb_metrics.top5_units:
            healthy = [
                u for u in wb_metrics.top5_units
                if u.net_income > 0
            ]
            if healthy:
                best = max(healthy, key=lambda u: u.net_income)
                green += (
                    f"Масштабируйте <b>{_escape_verdict(best.label)}</b> "
                    f"(<code>{_fmt_rub_in_code(best.net_income)}</code>/шт.) — "
                    "единственный здоровый драйвер маржи."
                )
            else:
                green += "сначала восстановите выкуп и маржу — масштабировать пока нечего."
        else:
            green += (
                "сначала поднимите выкуп по карточкам — масштабирование отложите."
            )
    elif prompt_metrics.abc_a_leader_margin > 0:
        green += (
            f"Лидер группы A — <b>{leader_name}</b> (Арт: <code>{leader_art}</code>), "
            f"маржа <code>{_fmt_rub_in_code(prompt_metrics.abc_a_leader_margin)}</code> руб., "
            f"выкуп <code>{leader_buyout:.1f}%</code>. "
            "Масштабируйте закуп и рекламу на этот SKU."
        )
    else:
        green += (
            f"Лидер A <b>{leader_name}</b> (Арт: <code>{leader_art}</code>) — "
            f"выкуп <code>{leader_buyout:.1f}%</code> под контролем."
        )

    yellow = "🟡 <b>ЗОНА ВНИМАНИЯ:</b> "
    if wb_metrics and prompt_metrics.adv_load_pct > _HIGH_DRR_PCT:
        yellow += (
            f"ДРР <code>{prompt_metrics.adv_load_pct:.1f}%</code> выше нормы — "
            "сузьте ключевые слова и отключите слабые кампании."
        )
    elif wb_metrics and 45 <= wb_metrics.buyout_coef_pct < 65:
        yellow += (
            f"выкуп <code>{wb_metrics.buyout_coef_pct:.1f}%</code> — подтяните инфографику, "
            "отзывы и размерную сетку."
        )
    elif wb_metrics and 12 < wb_metrics.ad_load_pct <= _HIGH_DRR_PCT:
        yellow += (
            f"реклама <code>{wb_metrics.ad_load_pct:.1f}%</code> — еженедельно отключайте "
            "кампании с ДРР выше целевого."
        )
    else:
        yellow += (
            "контролируйте оборачиваемость и не раздувайте склад неликвидом без спроса."
        )

    red = "🔴 <b>КРИТИЧЕСКАЯ ЗОНА:</b> "
    red_parts: list[str] = []
    if leader_is_critical:
        red_parts.append(
            f"<b>{leader_name}</b> (Арт: <code>{leader_art}</code>) — выкуп "
            f"<code>{leader_buyout:.1f}%</code>: нельзя масштабировать, сначала карточка и логистика."
        )
    if prompt_metrics.outsider_name not in ("—", "") and prompt_metrics.outsider_loss > 0:
        red_parts.append(
            f"Аутсайдер <b>{_escape_verdict(prompt_metrics.outsider_name)}</b> "
            f"(Арт: <code>{_escape_verdict(prompt_metrics.outsider_article)}</code>) — убыток "
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
    if prompt_metrics.adv_load_pct > _HIGH_DRR_PCT and not leader_is_critical:
        red_parts.append(
            f"ДРР <code>{prompt_metrics.adv_load_pct:.1f}%</code> — реклама съедает прибыль."
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
) -> str | None:
    """
    CFO-отчёт для wb_ozon_finance — всегда локальный шаблон (без OpenRouter).

    Гарантирует актуальные заголовки и формулировки без риска «старого» текста модели.
    """
    del settings, models, http_client  # OpenRouter отключён для стабильности отчёта
    if wb_metrics is None and matrix_rows:
        wb_metrics = resolve_wb_metrics_for_rows(matrix_rows, revenue_total)
    prompt_metrics = compute_wb_finance_prompt_metrics(
        revenue_total, wb_metrics, matrix_rows=matrix_rows
    )
    if prompt_metrics is None:
        return None
    local = build_wb_finance_express_html_local(prompt_metrics, wb_metrics)
    return append_wb_finance_mini_app_cta(local)


def resolve_wb_metrics_for_rows(
    rows: list[list[str]],
    revenue_total: float,
) -> WbMarketplaceMetrics | None:
    """Локальные метрики WB/Ozon для промпта и fallback."""
    return compute_wb_marketplace_metrics(rows, revenue_total=revenue_total)
