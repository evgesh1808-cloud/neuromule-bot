"""Тесты финансового пульса /admin_stats."""

from __future__ import annotations

from config import Settings
from services import metrics
from services.admin_openrouter_stats import (
    build_financial_pulse_report,
    format_financial_pulse_html,
    is_financial_stats_owner,
)


def test_is_financial_stats_owner_by_admin_telegram_id() -> None:
    cfg = Settings().model_copy(update={"admin_telegram_id": 424242, "admin_ids": [111, 424242]})
    assert is_financial_stats_owner(424242, cfg=cfg)
    assert not is_financial_stats_owner(111, cfg=cfg)


def test_is_financial_stats_owner_fallback_first_admin_id() -> None:
    cfg = Settings().model_copy(update={"admin_telegram_id": 0, "admin_ids": [777001, 888002]})
    assert is_financial_stats_owner(777001, cfg=cfg)
    assert not is_financial_stats_owner(888002, cfg=cfg)


def test_build_financial_pulse_report_costs() -> None:
    metrics.reset()
    metrics.observe(
        "openrouter.prompt_tokens",
        1_000_000,
        {"model": "deepseek/deepseek-chat", "role": "standard"},
    )
    metrics.observe(
        "openrouter.completion_tokens",
        1_000_000,
        {"model": "deepseek/deepseek-chat", "role": "standard"},
    )
    metrics.observe(
        "openrouter.prompt_tokens",
        2_000_000,
        {"model": "google/gemini-2.5-flash", "role": "table_generator"},
    )
    metrics.observe(
        "openrouter.completion_tokens",
        1_000_000,
        {"model": "google/gemini-2.5-flash", "role": "table_generator"},
    )
    metrics.incr("chat.success", {"role": "table_generator"}, value=3)
    metrics.incr("billing.spent_energy", {"role": "table_generator"}, value=60)
    metrics.incr("billing.spent_crystals", {"role": "table_generator"}, value=30)

    cfg = Settings().model_copy(
        update={
            "admin_stats_usd_rub_rate": 100.0,
            "openrouter_gemini_billable": True,
        }
    )
    report = build_financial_pulse_report(cfg=cfg)

    assert report.deepseek.prompt_tokens == 1_000_000
    assert report.deepseek.completion_tokens == 1_000_000
    assert report.gemini.prompt_tokens == 2_000_000
    assert report.gemini.request_count == 1
    assert report.successes_by_role["table_generator"] == 3
    assert report.table_spent_energy == 60
    assert report.table_spent_crystals == 30

    # deepseek: 0.14 + 0.28 = 0.42 USD; gemini billable: 0.075*2 + 0.30 = 0.45
    assert abs(report.total_cost_usd - 0.42 - 0.45) < 0.001
    assert "ФИНАНСОВЫЙ ПУЛЬТ" in format_financial_pulse_html(report)
    metrics.reset()


def test_gemini_free_gateway_zero_cost() -> None:
    metrics.reset()
    metrics.observe(
        "openrouter.prompt_tokens",
        5_000_000,
        {"model": "google/gemini-2.5-flash", "role": "standard"},
    )
    cfg = Settings().model_copy(update={"openrouter_gemini_billable": False})
    report = build_financial_pulse_report(cfg=cfg)
    assert report.total_cost_usd == 0.0
    metrics.reset()
