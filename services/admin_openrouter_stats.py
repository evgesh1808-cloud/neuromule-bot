"""Финансовый пульс OpenRouter: агрегация in-memory метрик и расчёт себестоимости."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from config import Settings, settings
from services import metrics

_LABEL_RE = re.compile(r"^(.+?)\{(.+)\}$")

# OpenRouter pricing (USD per 1M tokens).
_DEEPSEEK_CHAT = "deepseek/deepseek-chat"
_GEMINI_FLASH = "google/gemini-2.5-flash"

_DEEPSEEK_INPUT_USD_PER_M = 0.14
_DEEPSEEK_OUTPUT_USD_PER_M = 0.28
_GEMINI_INPUT_USD_PER_M = 0.075
_GEMINI_OUTPUT_USD_PER_M = 0.30

_TABLE_ROLE = "table_generator"


@dataclass(frozen=True)
class ModelTokenTotals:
    """Суммарные токены и число запросов (по histogram count) для одной модели."""

    prompt_tokens: int
    completion_tokens: int
    request_count: int


@dataclass(frozen=True)
class FinancialPulseReport:
    gemini: ModelTokenTotals
    deepseek: ModelTokenTotals
    successes_by_role: dict[str, int]
    total_spent_energy: int
    total_spent_crystals: int
    table_spent_energy: int
    table_spent_crystals: int
    total_cost_usd: float
    total_cost_rub: float
    table_cost_usd: float
    table_cost_rub: float
    table_margin_percent: float | None
    usd_rub_rate: float


def _parse_metric_key(key: str) -> tuple[str, dict[str, str]]:
    match = _LABEL_RE.match(key)
    if not match:
        return key, {}
    name = match.group(1)
    labels: dict[str, str] = {}
    for chunk in match.group(2).split(","):
        if "=" not in chunk:
            continue
        label_key, label_val = chunk.split("=", 1)
        labels[label_key.strip()] = label_val.strip()
    return name, labels


def _hist_sum(histograms: dict[str, dict[str, Any]], metric_name: str) -> dict[str, float]:
    """Суммирует histogram ``sum`` по всем label-комбинациям метрики."""
    out: dict[str, float] = {}
    for key, hist in histograms.items():
        name, labels = _parse_metric_key(key)
        if name != metric_name:
            continue
        model = labels.get("model", "")
        out[model] = out.get(model, 0.0) + float(hist.get("sum") or 0)
    return out


def _hist_count_by_model(histograms: dict[str, dict[str, Any]], metric_name: str) -> dict[str, int]:
    """Число запросов (histogram count) по модели."""
    out: dict[str, int] = {}
    for key, hist in histograms.items():
        name, labels = _parse_metric_key(key)
        if name != metric_name:
            continue
        model = labels.get("model", "")
        out[model] = out.get(model, 0) + int(hist.get("count") or 0)
    return out


def _tokens_for_model(
    histograms: dict[str, dict[str, Any]],
    model_id: str,
) -> ModelTokenTotals:
    prompt_by_model = _hist_sum(histograms, "openrouter.prompt_tokens")
    completion_by_model = _hist_sum(histograms, "openrouter.completion_tokens")
    counts = _hist_count_by_model(histograms, "openrouter.prompt_tokens")
    return ModelTokenTotals(
        prompt_tokens=int(prompt_by_model.get(model_id, 0)),
        completion_tokens=int(completion_by_model.get(model_id, 0)),
        request_count=int(counts.get(model_id, 0)),
    )


def _counter_by_role(counters: dict[str, int], metric_name: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, value in counters.items():
        name, labels = _parse_metric_key(key)
        if name != metric_name:
            continue
        role = labels.get("role", "unknown")
        out[role] = out.get(role, 0) + int(value)
    return out


def _counter_total(counters: dict[str, int], metric_name: str, *, role: str | None = None) -> int:
    total = 0
    for key, value in counters.items():
        name, labels = _parse_metric_key(key)
        if name != metric_name:
            continue
        if role is not None and labels.get("role") != role:
            continue
        total += int(value)
    return total


def _model_cost_usd(
    prompt_tokens: int,
    completion_tokens: int,
    *,
    input_usd_per_m: float,
    output_usd_per_m: float,
    billable: bool = True,
) -> float:
    if not billable:
        return 0.0
    return (prompt_tokens / 1_000_000) * input_usd_per_m + (
        completion_tokens / 1_000_000
    ) * output_usd_per_m


def _role_cost_usd_from_histograms(
    histograms: dict[str, dict[str, Any]],
    *,
    role: str,
    gemini_billable: bool,
) -> float:
    cost = 0.0
    for key, hist in histograms.items():
        name, labels = _parse_metric_key(key)
        if labels.get("role") != role:
            continue
        model = labels.get("model", "")
        tokens = int(hist.get("sum") or 0)
        if name == "openrouter.prompt_tokens":
            if model == _DEEPSEEK_CHAT:
                cost += _model_cost_usd(
                    tokens, 0,
                    input_usd_per_m=_DEEPSEEK_INPUT_USD_PER_M,
                    output_usd_per_m=_DEEPSEEK_OUTPUT_USD_PER_M,
                )
            elif model == _GEMINI_FLASH:
                cost += _model_cost_usd(
                    tokens, 0,
                    input_usd_per_m=_GEMINI_INPUT_USD_PER_M,
                    output_usd_per_m=_GEMINI_OUTPUT_USD_PER_M,
                    billable=gemini_billable,
                )
        elif name == "openrouter.completion_tokens":
            if model == _DEEPSEEK_CHAT:
                cost += _model_cost_usd(
                    0, tokens,
                    input_usd_per_m=_DEEPSEEK_INPUT_USD_PER_M,
                    output_usd_per_m=_DEEPSEEK_OUTPUT_USD_PER_M,
                )
            elif model == _GEMINI_FLASH:
                cost += _model_cost_usd(
                    0, tokens,
                    input_usd_per_m=_GEMINI_INPUT_USD_PER_M,
                    output_usd_per_m=_GEMINI_OUTPUT_USD_PER_M,
                    billable=gemini_billable,
                )
    return cost


def _total_api_cost_usd(
    gemini: ModelTokenTotals,
    deepseek: ModelTokenTotals,
    *,
    gemini_billable: bool,
) -> float:
    gemini_cost = _model_cost_usd(
        gemini.prompt_tokens,
        gemini.completion_tokens,
        input_usd_per_m=_GEMINI_INPUT_USD_PER_M,
        output_usd_per_m=_GEMINI_OUTPUT_USD_PER_M,
        billable=gemini_billable,
    )
    deepseek_cost = _model_cost_usd(
        deepseek.prompt_tokens,
        deepseek.completion_tokens,
        input_usd_per_m=_DEEPSEEK_INPUT_USD_PER_M,
        output_usd_per_m=_DEEPSEEK_OUTPUT_USD_PER_M,
    )
    return gemini_cost + deepseek_cost


def _estimate_table_margin_percent(
    table_energy: int,
    table_crystals: int,
    table_cost_rub: float,
    *,
    cfg: Settings,
) -> float | None:
    """Грубая оценка маржи таблиц: выручка по пакету MINI vs себестоимость API."""
    if table_energy <= 0 and table_crystals <= 0:
        return None
    rub_per_energy = (cfg.mini_rub_kopecks / 100.0) / max(cfg.mini_energy, 1)
    rub_per_crystal = (cfg.mini_rub_kopecks / 100.0) / max(cfg.mini_crystals, 1)
    revenue_rub = table_energy * rub_per_energy + table_crystals * rub_per_crystal
    if revenue_rub <= 0:
        return None
    margin = (revenue_rub - table_cost_rub) / revenue_rub * 100.0
    return max(margin, 0.0)


def build_financial_pulse_report(
    snap: dict[str, dict[str, Any]] | None = None,
    *,
    cfg: Settings | None = None,
) -> FinancialPulseReport:
    """Собирает отчёт из ``metrics.snapshot()``."""
    cfg = cfg or settings
    data = snap if snap is not None else metrics.snapshot()
    counters = data.get("counters") or {}
    histograms = data.get("histograms") or {}

    gemini = _tokens_for_model(histograms, _GEMINI_FLASH)
    deepseek = _tokens_for_model(histograms, _DEEPSEEK_CHAT)
    gemini_billable = bool(cfg.openrouter_gemini_billable)

    total_cost_usd = _total_api_cost_usd(gemini, deepseek, gemini_billable=gemini_billable)
    table_cost_usd = _role_cost_usd_from_histograms(
        histograms,
        role=_TABLE_ROLE,
        gemini_billable=gemini_billable,
    )
    usd_rub = float(cfg.admin_stats_usd_rub_rate)

    table_energy = _counter_total(counters, "billing.spent_energy", role=_TABLE_ROLE)
    table_crystals = _counter_total(counters, "billing.spent_crystals", role=_TABLE_ROLE)

    return FinancialPulseReport(
        gemini=gemini,
        deepseek=deepseek,
        successes_by_role=_counter_by_role(counters, "chat.success"),
        total_spent_energy=_counter_total(counters, "billing.spent_energy"),
        total_spent_crystals=_counter_total(counters, "billing.spent_crystals"),
        table_spent_energy=table_energy,
        table_spent_crystals=table_crystals,
        total_cost_usd=total_cost_usd,
        total_cost_rub=total_cost_usd * usd_rub,
        table_cost_usd=table_cost_usd,
        table_cost_rub=table_cost_usd * usd_rub,
        table_margin_percent=_estimate_table_margin_percent(
            table_energy,
            table_crystals,
            table_cost_usd * usd_rub,
            cfg=cfg,
        ),
        usd_rub_rate=usd_rub,
    )


def format_financial_pulse_html(report: FinancialPulseReport) -> str:
    """HTML-сообщение для владельца продукта (Telegram)."""
    table_success = report.successes_by_role.get(_TABLE_ROLE, 0)
    margin_line = (
        f"• Чистая маржа на таблицах: <code>&gt; {report.table_margin_percent:.1f}%</code> 🚀"
        if report.table_margin_percent is not None
        else "• Чистая маржа на таблицах: <i>недостаточно данных</i>"
    )
    return (
        "📊 <b>ФИНАНСОВЫЙ ПУЛЬТ УПРАВЛЕНИЯ БОТОМ</b>\n"
        "───────────────────\n"
        "🤖 <b>Использование моделей:</b>\n"
        f"• Gemini Flash: {report.gemini.request_count} запросов\n"
        f"• DeepSeek V3 (Резерв): {report.deepseek.request_count} запросов\n\n"
        "📈 <b>Расход токенов (DeepSeek):</b>\n"
        f"• Входные (Prompt): {report.deepseek.prompt_tokens:,}\n"
        f"• Выходные (Completion): {report.deepseek.completion_tokens:,}\n\n"
        "📋 <b>Успешные генерации:</b>\n"
        f"• Таблицы (table_generator): {table_success}\n"
        f"• Всего по ролям: {sum(report.successes_by_role.values()):,}\n\n"
        "💵 <b>Себестоимость OpenRouter:</b>\n"
        f"• Всего потрачено: <code>${report.total_cost_usd:.4f}</code> "
        f"(~{report.total_cost_rub:.2f} руб.)\n"
        f"• Из них на Таблицы: <code>${report.table_cost_usd:.4f}</code>\n\n"
        "💎 <b>Оценочная маржинальность:</b>\n"
        f"• Списано у пользователей: {report.total_spent_energy} ⚡ / "
        f"{report.total_spent_crystals} 💎\n"
        f"{margin_line}\n"
        "───────────────────\n"
        "<i>Обновлено мгновенно из in-memory счетчиков.</i>"
    )


def is_financial_stats_owner(user_id: int, *, cfg: Settings | None = None) -> bool:
    """Доступ к ``/admin_stats``: только ``ADMIN_TELEGRAM_ID`` (или первый ``ADMIN_IDS``)."""
    cfg = cfg or settings
    owner_id = int(cfg.admin_telegram_id or 0)
    if owner_id > 0:
        return user_id == owner_id
    admin_ids = [int(x) for x in (cfg.admin_ids or []) if int(x) > 0]
    return bool(admin_ids) and user_id == admin_ids[0]
