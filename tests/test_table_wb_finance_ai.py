"""Промпт и метрики ИИ-консалтинга WB/Ozon finance."""

from __future__ import annotations

import json

import pytest

from content.chat_prompt import WB_ANALYTICS_SYSTEM_PROMPT, build_wb_marketplace_finance_system_prompt
from services.table_text_response import compute_wb_marketplace_metrics
from services.table_wb_finance_ai import (
    append_wb_finance_mini_app_cta,
    build_wb_finance_json_user_message,
    build_wb_finance_openrouter_prompt_pair,
    build_wb_finance_system_prompt_from_totals,
    build_wb_mpstats_ai_context,
    compute_business_score,
    compute_fomo_lost_rub,
    compute_wb_finance_prompt_metrics,
    derive_business_verdict,
)


def test_wb_analytics_system_prompt_cfo_v8_static() -> None:
    prompt = build_wb_marketplace_finance_system_prompt()
    assert prompt is WB_ANALYTICS_SYSTEM_PROMPT
    assert "MPSTATS" in prompt or "mpstats" in prompt.lower()
    assert "Проблемные зоны" in prompt
    assert "Балласт" in prompt
    assert "неликвид" in prompt.lower()
    assert "Senior ИИ-Аналитик" not in prompt
    assert "ИНДЕКС ЗДОРОВЬЯ БИЗНЕСА" in prompt
    assert "СВЕТОФОР ЭФФЕКТИВНОСТИ" in prompt
    assert "КАЛЬКУЛЯТОР ПОТЕРЬ" in prompt
    assert "2000 символов" in prompt
    assert "ABC-АНАЛИЗ ПРОДАЖ" in prompt
    assert "BENOVY" not in prompt
    assert "cfo-v8" in prompt
    assert "20%" in prompt
    assert "group_A" in prompt
    assert "loss_calculator" in prompt
    assert "traffic_light" in prompt
    assert "health_index" in prompt
    assert "СЛУЖЕБНЫЕ ПРАВИЛА" not in prompt
    assert "ПЛАН ДЕЙСТВИЙ ДЛЯ ПРЕДПРИНИМАТЕЛЯ" in prompt
    assert "ГЛАВНЫЙ АНАЛИТИЧЕСКИЙ ВЫВОД" in prompt
    assert "ГЛАВНЫЙ ВЫВОД ИИ" not in prompt
    assert "ОБЩАЯ ВЫРУЧКА" in prompt
    assert "ИИ-ПЛАН" not in prompt


def test_build_wb_finance_json_user_message_wraps_payload() -> None:
    payload = {"finance": {"total_revenue": 1000.0}}
    user = build_wb_finance_json_user_message(payload)
    assert "1000.0" in user
    assert "Python" in user
    assert "ИИ»" in user or "«ИИ»" in user


def test_build_wb_finance_openrouter_prompt_pair_from_matrix() -> None:
    matrix = [
        [
            "Бренд",
            "Артикул",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
            "Остаток на складе, шт.",
        ],
        ["ACME", "SKU-A1", "70", "90", "5", "3500", "100000", "30"],
    ]
    pair = build_wb_finance_openrouter_prompt_pair(matrix, revenue_total=100_000.0)
    assert pair is not None
    system, user = pair
    assert "cfo-v8" in system
    data = json.loads(user.split("\n\n", 1)[-1])
    assert data["finance"]["total_revenue"] == 100_000.0
    assert "health_index" in data
    assert "traffic_light" in data


def test_compute_wb_finance_prompt_metrics_from_etl() -> None:
    matrix = [
        [
            "Предмет",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
            "Остаток на складе, шт.",
        ],
        ["Футболка Premium", "70", "90", "5", "3500", "100000", "30"],
        ["DEAD", "0", "10", "0", "500", "0", "100"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=100_000.0)
    metrics = compute_wb_finance_prompt_metrics(100_000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    assert metrics.revenue == 100_000.0
    assert metrics.business_score >= 1.0


def test_build_wb_finance_system_prompt_from_totals() -> None:
    wb = compute_wb_marketplace_metrics(
        [
            ["Предмет", "К перечислению, руб."],
            ["A", "100000"],
        ],
        revenue_total=100_000.0,
    )
    system = build_wb_finance_system_prompt_from_totals(100_000.0, wb)
    assert system is not None
    assert "cfo-v8" in system


def test_compute_business_score_bounds() -> None:
    high = compute_business_score(
        profitability_pct=20.0,
        ad_load_pct=10.0,
        buyout_coef_pct=75.0,
        worst_unit_net=50.0,
    )
    assert 8.0 <= high <= 10.0
    low = compute_business_score(
        profitability_pct=2.0,
        ad_load_pct=30.0,
        buyout_coef_pct=30.0,
        worst_unit_net=-100.0,
    )
    assert 1.0 <= low <= 5.0


def test_derive_business_verdict_high_score() -> None:
    verdict = derive_business_verdict(
        business_score=8.5,
        profitability_pct=20.0,
        ad_load_pct=10.0,
        buyout_coef_pct=70.0,
        worst_unit_label=None,
    )
    assert "масштабирован" in verdict.lower() or "маржинальность" in verdict.lower()


def test_compute_fomo_lost_rub() -> None:
    wb = compute_wb_marketplace_metrics(
        [
            ["Предмет", "К перечислению, руб.", "Выкупили, шт.", "Доставки, шт.", "Возвраты, шт."],
            ["A", "100000", "30", "100", "20"],
        ],
        revenue_total=100_000.0,
    )
    lost, parts = compute_fomo_lost_rub(100_000.0, wb)
    assert lost >= 0.0
    assert isinstance(parts, tuple)


def test_append_wb_finance_mini_app_cta_empty() -> None:
    html = append_wb_finance_mini_app_cta("<b>test</b>")
    assert "test" in html


def test_sanitize_wb_finance_html_strips_technical_parentheses() -> None:
    from services.table_wb_finance_ai import sanitize_wb_finance_html

    raw = (
        "📦 ABC-АНАЛИЗ МАТРИЦЫ (локальный ETL, не пересчитывай)\n"
        "🟢 ЗОНА УСПЕХА (по 2–3 предложения на зону, без воды)\n"
        "📋 ПЛАН (каждый шаг — 1 предложение)"
    )
    cleaned = sanitize_wb_finance_html(raw)
    assert "локальный ETL" not in cleaned
    assert "по 2–3 предложения" not in cleaned
    assert "каждый шаг" not in cleaned
    assert "ABC-АНАЛИЗ ПРОДАЖ" in cleaned


def test_build_wb_mpstats_ai_context_full_group_c() -> None:
    matrix = [
        [
            "Бренд",
            "Артикул",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
            "Остаток на складе, шт.",
        ],
        ["A", "1", "50", "60", "2", "1000", "80000", "10"],
        ["B", "2", "10", "20", "1", "500", "15000", "5"],
        ["C", "3", "5", "10", "0", "200", "5000", "100"],
    ]
    ctx = build_wb_mpstats_ai_context(matrix, revenue_total=100_000.0)
    assert len(ctx["abc_analysis"]["group_C"]) == ctx["abc_analysis"]["total_group_c_count"]
    assert isinstance(ctx["problem_zones"]["ballast"], list)


def test_build_matrix_problem_zones_block() -> None:
    from services.table_wb_finance_ai import build_matrix_problem_zones_block
    from services.file_processor import compute_seller_matrix_etl

    matrix = [
        [
            "Предмет",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
            "Остаток на складе, шт.",
        ],
        ["DEAD", "0", "10", "5", "500", "0", "100"],
    ]
    etl = compute_seller_matrix_etl(matrix, revenue_total=0.0)
    block = build_matrix_problem_zones_block(etl)
    assert "Неликвид" in block or "Балласт" in block or "проблемных" in block.lower()


def test_build_wb_finance_express_html_local_abc_header() -> None:
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [
        ["Предмет", "К перечислению, руб.", "Выкупили, шт.", "Доставки, шт.", "Возвраты, шт."],
        ["WRAPPER", "100000", "70", "90", "5"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=100_000.0)
    metrics = compute_wb_finance_prompt_metrics(100_000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    html = build_wb_finance_express_html_local(metrics, None)
    assert "ABC-АНАЛИЗ ПРОДАЖ" in html
    assert "cfo-v8" in html


def test_build_wb_finance_express_html_local_no_ii_word() -> None:
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [
        ["Предмет", "К перечислению, руб."],
        ["A", "50000"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=50_000.0)
    metrics = compute_wb_finance_prompt_metrics(50_000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    html = build_wb_finance_express_html_local(metrics, wb)
    assert "ИИ-Аналитик" not in html
    assert "ИИ-ПЛАН" not in html


def test_build_wb_finance_express_html_local_traffic_light() -> None:
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [
        [
            "Предмет",
            "К перечислению, руб.",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
        ],
        ["GOOD", "90000", "70", "90", "5", "3000"],
        ["BAD", "10000", "0", "20", "10", "800"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=100_000.0)
    metrics = compute_wb_finance_prompt_metrics(100_000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    html = build_wb_finance_express_html_local(metrics, wb)
    assert "СВЕТОФОР ЭФФЕКТИВНОСТИ" in html
    assert "ЗОНА УСПЕХА" in html


def test_build_wb_finance_express_html_local_loss_calculator() -> None:
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [
        [
            "Предмет",
            "К перечислению, руб.",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
        ],
        ["RET", "50000", "10", "30", "15", "2000"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=50_000.0)
    metrics = compute_wb_finance_prompt_metrics(50_000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    html = build_wb_finance_express_html_local(metrics, wb)
    assert "КАЛЬКУЛЯТОР ПОТЕРЬ" in html
    assert "Логистика возвратов" in html or "покатуш" in html.lower()


def test_build_wb_finance_express_html_local_health_index() -> None:
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [
        ["Предмет", "К перечислению, руб."],
        ["A", "100000"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=100_000.0)
    metrics = compute_wb_finance_prompt_metrics(100_000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    html = build_wb_finance_express_html_local(metrics, None)
    assert "ИНДЕКС ЗДОРОВЬЯ" in html


def test_build_wb_finance_express_html_local_matrix_zones() -> None:
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [
        [
            "Предмет",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
            "Остаток на складе, шт.",
        ],
        ["DEAD", "0", "10", "5", "500", "0", "100"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=1.0)
    metrics = compute_wb_finance_prompt_metrics(1.0, wb, matrix_rows=matrix)
    assert metrics is not None
    html = build_wb_finance_express_html_local(metrics, None)
    assert "Проблемные зоны" in html
    assert "Неликвид" in html or "Балласт" in html or "неликвид" in html.lower()


def test_build_wb_finance_express_html_local_plan() -> None:
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [["Предмет", "К перечислению, руб."], ["A", "10000"]]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=10_000.0)
    metrics = compute_wb_finance_prompt_metrics(10_000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    html = build_wb_finance_express_html_local(metrics, None)
    assert "ПЛАН ДЕЙСТВИЙ" in html
