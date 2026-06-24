"""ИИ-консалтинг для под-режима wb_ozon_finance (метрики ETL → OpenRouter)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

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
_WB_FINANCE_AI_TEMPERATURE = 0.45
_WB_FINANCE_MAX_OUTPUT_TOKENS = 1400
_WB_FINANCE_TELEGRAM_SOFT_MAX_CHARS = 2000
_FINANCE_SEPARATOR = "────────────────────────"
_OLD_FINALE_MARKERS = (
    "Финальный Excel",
    "интерактивный дашборд",
    "Автопилот по API",
    "Хватит загружать",
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
) -> WbFinancePromptMetrics | None:
    """Собирает переменные ETL для system/user prompt и локального fallback."""
    if revenue_total <= 0:
        return None
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
        total_ad_cost=wb_metrics.total_advertising_cost if wb_metrics else 0.0,
        sales_qty=wb_metrics.sales_qty if wb_metrics else 0.0,
        returns_qty=wb_metrics.returns_qty if wb_metrics else 0.0,
        deliveries_qty=wb_metrics.deliveries_qty if wb_metrics else 0.0,
    )


def _prompt_kwargs_from_metrics(metrics: WbFinancePromptMetrics) -> dict[str, str]:
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
        "etl_source": "local_python_wb_ozon_finance",
        "revenue_rub": round(prompt_metrics.revenue, 2),
        "tax_usn_6pct_rub": round(prompt_metrics.tax, 2),
        "clear_profit_rub": round(prompt_metrics.clear_profit, 2),
        "profitability_pct": round(prompt_metrics.profitability_pct, 1),
        "business_score": prompt_metrics.business_score,
        "business_verdict": prompt_metrics.verdict,
        "fomo_lost_rub": round(prompt_metrics.fomo_lost_rub, 2),
        "fomo_breakdown": list(prompt_metrics.fomo_breakdown),
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
        "Ниже — подтверждённые метрики локального ETL-расчёта NeuroMule. "
        "Сформируй финансовый экспресс-отчёт строго по структуре из system prompt. "
        "Числа выручки, налога, прибыли, рентабельности, скоринга и FOMO не меняй. "
        "Пиши ёмко: весь ответ до 2000 символов, зоны светофора — по 2–3 предложения.\n\n"
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
    return build_wb_marketplace_finance_system_prompt(**_prompt_kwargs_from_metrics(metrics))


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
    """Премиальный локальный отчёт (0 ₽ OpenRouter) — тот же стиль, что и ИИ."""
    lines = [
        "📊 <b>ФИНАНСОВЫЙ ЭКСПРЕСС-АНАЛИЗ БИЗНЕСА</b>",
        _FINANCE_SEPARATOR,
        (
            f"🎯 <b>БИЗНЕС-СКОРИНГ МАГАЗИНА:</b> "
            f"<code>{prompt_metrics.business_score:.1f} / 10</code>"
        ),
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
        "📈 <b>СВЕТОФОР ЗДОРОВЬЯ БИЗНЕСА</b>",
    ]
    for zone_line in _build_traffic_light_block(wb_metrics, prompt_metrics):
        lines.append(zone_line)
    lines.extend(
        [
            _FINANCE_SEPARATOR,
            "💸 <b>КАЛЬКУЛЯТОР УПУЩЕННОЙ ВЫГОДЫ (FOMO)</b>",
            (
                f"Серверный расчёт: <code>{_fmt_rub_in_code(prompt_metrics.fomo_lost_rub)} руб.</code>"
            ),
        ]
    )
    if prompt_metrics.fomo_breakdown:
        for part in prompt_metrics.fomo_breakdown:
            lines.append(f"<i>• {part}</i>")
    else:
        lines.append(
            "<i>Критических зон упущенной выгоды не выявлено — удерживайте текущую дисциплину.</i>"
        )
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
        ]
    )
    return append_wb_finance_mini_app_cta("\n".join(lines))


def _escape_verdict(text: str) -> str:
    from services.telegram_safe_text import _escape_telegram_html

    return _escape_telegram_html(text)


def _build_traffic_light_block(
    wb_metrics: WbMarketplaceMetrics | None,
    prompt_metrics: WbFinancePromptMetrics,
) -> list[str]:
    """🟢🟡🔴 блоки для локального fallback."""
    green = (
        f"🟢 <b>ЗОНА УСПЕХА:</b> Рентабельность "
        f"<code>{prompt_metrics.profitability_pct:.1f}%</code> — "
    )
    if wb_metrics and wb_metrics.top5_units:
        best = max(wb_metrics.top5_units, key=lambda u: u.net_income)
        green += (
            f"лидер «{best.label}» даёт <code>{_fmt_rub_in_code(best.net_income)}</code>/шт. "
            "Масштабируйте закуп и рекламу на этот SKU."
        )
    else:
        green += "базовая экономика позволяет тестировать масштабирование лидеров."

    yellow = f"🟡 <b>ЗОНА ВНИМАНИЯ:</b> "
    if wb_metrics and 45 <= wb_metrics.buyout_coef_pct < 65:
        yellow += (
            f"выкуп <code>{wb_metrics.buyout_coef_pct:.1f}%</code> — подтяните инфографику, "
            "отзывы и размерную сетку."
        )
    elif wb_metrics and 12 < wb_metrics.ad_load_pct <= 22:
        yellow += (
            f"реклама <code>{wb_metrics.ad_load_pct:.1f}%</code> — еженедельно отключайте "
            "кампании с ДРР выше целевого."
        )
    else:
        yellow += (
            "контролируйте оборачиваемость и не раздувайте склад неликвидом без спроса."
        )

    red = "🔴 <b>КРИТИЧЕСКАЯ ЗОНА:</b> "
    if wb_metrics and wb_metrics.top5_units:
        worst = min(wb_metrics.top5_units, key=lambda u: u.net_income)
        if worst.net_income < 0:
            red += (
                f"«{worst.label}» убыточен "
                f"(<code>{_fmt_rub_in_code(worst.net_income)}</code>/шт.) — "
                "вымывает оборотные средства и кассу."
            )
        elif wb_metrics.buyout_coef_pct > 0 and wb_metrics.buyout_coef_pct < 45:
            red += (
                f"выкуп <code>{wb_metrics.buyout_coef_pct:.1f}%</code> — возвраты и покатушки "
                "бьют по марже сильнее рекламы."
            )
        elif wb_metrics.ad_load_pct > 25:
            red += (
                f"ДРР <code>{wb_metrics.ad_load_pct:.1f}%</code> — реклама съедает "
                "чистую прибыль быстрее, чем растёт выручка."
            )
        else:
            red += "критических утечек не зафиксировано — держите фокус на жёлтой зоне."
    else:
        red += "загрузите полный отчёт реализации для детекции убыточных SKU."

    return [green, "", yellow, "", red]


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

    system = build_wb_marketplace_finance_system_prompt(**_prompt_kwargs_from_metrics(prompt_metrics))
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
    return append_wb_finance_mini_app_cta(repair_telegram_html(content))


def resolve_wb_metrics_for_rows(
    rows: list[list[str]],
    revenue_total: float,
) -> WbMarketplaceMetrics | None:
    """Локальные метрики WB/Ozon для промпта и fallback."""
    return compute_wb_marketplace_metrics(rows, revenue_total=revenue_total)
