"""Промпт и метрики ИИ-консалтинга WB/Ozon finance."""

from __future__ import annotations

import pytest

from content.chat_prompt import build_wb_marketplace_finance_system_prompt
from services.table_text_response import compute_wb_marketplace_metrics
from services.table_wb_finance_ai import (
    append_wb_finance_mini_app_cta,
    build_wb_finance_system_prompt_from_totals,
    build_wb_marketplace_finance_user_prompt,
    compute_business_score,
    compute_fomo_lost_rub,
    compute_wb_finance_prompt_metrics,
    derive_business_verdict,
)


def test_build_wb_marketplace_finance_system_prompt_variables() -> None:
    prompt = build_wb_marketplace_finance_system_prompt(
        revenue="185,000.00",
        tax="11,100.00",
        clear_profit="173,900.00",
        profitability_pct="94.0",
        adv_load="13.3",
        buy_ratio="72.5",
        year_forecast="2,220,000",
        business_score="7.5",
        verdict="Высокая маржинальность при контролируемом ДРР.",
        fomo_lost_rub="12,500.00",
        logistics_fomo_rub="3,200.00",
        abc_a_leader="WRAPPER",
        abc_a_leader_name="Футболка Premium",
        abc_a_leader_article="WRAPPER-001",
        abc_a_count="2",
        abc_c_count="1",
        abc_c_summary="• DEAD (арт. DEAD-99)",
        outsider_name="DEAD",
        outsider_article="DEAD-99",
        outsider_loss="400.00",
        outsider_buyout="0.0",
        abc_a_leader_buyout="77.8",
        fomo_details_block="• Возвраты 10 шт.\n• Рекламный перерасход",
        return_logistics_block=(
            "• Логистика возвратов: Стаканы (Арт: 100): 3187 возвратов × 52.40 руб. "
            "обратной логистики по литражу"
        ),
        reverse_logistics_avg_rub="52.40",
        sku_catalog_block="• Футболка Premium (Артикул: WRAPPER-001) — 100 000.00 руб. — 85 000.00 руб. — 77.8%",
        oos_forecast_line="«BOX» закончится через 3 дн. (риск OOS)",
        matrix_problem_zones_block=(
            "📉 Балласт:\n"
            "• DEAD (арт. DEAD-99) — выкуп 0.0% — покатушки генерируют расходы на обратную логистику"
        ),
        localization_index_line="не указан в исходных данных",
    )
    assert "автоматический финансовый модуль" in prompt.lower() or "оцифровки маркетплейсов" in prompt
    assert "Проблемные зоны и скрытые убытки" in prompt
    assert "Балласт" in prompt
    assert "Неликвид" in prompt
    assert "185,000.00 руб." in prompt
    assert "11,100.00 руб." in prompt
    assert "173,900.00 руб." in prompt
    assert "реклама 13.3%" in prompt or "ДРР 13.3%" in prompt
    assert "2,220,000 руб." in prompt
    assert "Senior ИИ-Аналитик" not in prompt
    assert "ИНДЕКС ЗДОРОВЬЯ БИЗНЕСА" in prompt
    assert "СВЕТОФОР ЭФФЕКТИВНОСТИ" in prompt
    assert "КАЛЬКУЛЯТОР ПОТЕРЬ" in prompt
    assert "12,500.00 руб." in prompt
    assert "2000 символов" in prompt
    assert "РЕЙТИНГ ПРОДАЖ ПО ТОВАРАМ" in prompt
    assert "Футболка Premium" in prompt
    assert "WRAPPER-001" in prompt
    assert "3,200.00" in prompt
    assert "КАТАЛОГ ТОВАРОВ ETL" not in prompt
    assert "Подключите" not in prompt
    assert "СЛУЖЕБНЫЕ ПРАВИЛА" in prompt
    assert "ПЛАН ДЕЙСТВИЙ ДЛЯ ПРЕДПРИНИМАТЕЛЯ" in prompt
    assert "ГЛАВНЫЙ АНАЛИТИЧЕСКИЙ ВЫВОД" in prompt
    assert "ГЛАВНЫЙ ВЫВОД ИИ" not in prompt
    assert "ОБЩАЯ ВЫРУЧКА" in prompt
    assert "ИИ-ПЛАН" not in prompt
    template_section = prompt.split("СТРУКТУРА ОТВЕТА", 1)[-1]
    assert "серверный" not in template_section.lower()
    assert "Серверный" not in template_section
    assert "ИИ-ПЛАН" not in template_section
    assert "лидер — всего" not in prompt
    assert "всего <code>" not in prompt
    assert "ABC-АНАЛИЗ МАТРИЦЫ (локальный" not in prompt
    assert "cfo-v7" in prompt
    assert "выкуп &lt;15%" in prompt or "15%" in prompt
    assert "20%" in prompt
    assert "• DEAD" in prompt
    assert "• Возвраты" in prompt
    assert "Логистика возвратов:" in prompt
    assert "52.40" in prompt
    assert "ОБРАТНАЯ ЛОГИСТИКА" in prompt
    assert "(каждый источник потерь" not in prompt
    assert "(каждый SKU с новой строки" not in prompt


def test_compute_wb_finance_prompt_metrics_from_etl() -> None:
    matrix = [
        [
            "Предмет",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Удержания за продвижение",
            "К перечислению, руб.",
        ],
        ["Футболка", "8", "9", "1", "500", "4000"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=6000.0)
    assert wb is not None
    metrics = compute_wb_finance_prompt_metrics(6000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    assert metrics.revenue == 6000.0
    assert metrics.tax == 360.0
    assert metrics.clear_profit == pytest.approx(3131.0)
    assert metrics.year_forecast == 72000.0
    assert metrics.profitability_pct == pytest.approx(52.2, abs=0.5)
    assert 1.0 <= metrics.business_score <= 10.0
    assert metrics.verdict
    assert metrics.fomo_lost_rub >= 0

    system = build_wb_finance_system_prompt_from_totals(6000.0, wb)
    assert system is not None
    assert "6,000.00 руб." in system
    assert f"{metrics.business_score:.1f}" in system

    user = build_wb_marketplace_finance_user_prompt(metrics, wb)
    assert "revenue_rub" in user
    assert "fomo_lost_rub" in user
    assert "sku_catalog" in user
    assert "outsider_sku" in user
    assert "Футболка" in user


def test_fomo_and_scoring_helpers() -> None:
    matrix = [
        [
            "Предмет",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Удержания за продвижение",
            "К перечислению, руб.",
            "Цена реализации",
            "Вознаграждение",
            "Логистика",
        ],
        ["Убыток", "2", "20", "8", "3000", "1000", "500", "400", "200"],
        ["Хит", "50", "55", "2", "500", "50000", "1200", "100", "50"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=51_000.0)
    assert wb is not None
    fomo, parts = compute_fomo_lost_rub(51_000.0, wb)
    assert fomo > 0
    assert parts
    score = compute_business_score(
        profitability_pct=90.0,
        ad_load_pct=wb.ad_load_pct,
        buyout_coef_pct=wb.buyout_coef_pct,
        worst_unit_net=min(u.net_income for u in wb.top5_units),
    )
    assert score >= 1.0
    verdict = derive_business_verdict(
        business_score=score,
        profitability_pct=90.0,
        ad_load_pct=wb.ad_load_pct,
        buyout_coef_pct=wb.buyout_coef_pct,
        worst_unit_label="Убыток",
    )
    assert verdict


def test_append_wb_finance_mini_app_cta_is_noop() -> None:
    body = "📊 <b>ФИНАНСОВЫЙ ЭКСПРЕСС-АНАЛИЗ</b>"
    out = append_wb_finance_mini_app_cta(body)
    assert out == body
    assert "Автопилот по API" not in out


def test_sanitize_wb_finance_html_replaces_legacy_headers() -> None:
    from services.table_wb_finance_ai import has_legacy_wb_finance_markers, sanitize_wb_finance_html

    raw = "📋 ИИ-План действий\n💸 Серверный расчёт: 1000"
    cleaned = sanitize_wb_finance_html(raw)
    assert "ИИ-План" not in cleaned
    assert "Серверный" not in cleaned
    assert "ПЛАН ДЕЙСТВИЙ" in cleaned
    assert has_legacy_wb_finance_markers(raw)
    assert not has_legacy_wb_finance_markers(cleaned)


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
    assert "РЕЙТИНГ ПРОДАЖ" in cleaned


def test_enrich_table_json_wb_finance_adds_abc_and_summary() -> None:
    from services.table_wb_finance_ai import enrich_table_json_wb_finance
    from services.table_text_response import compute_wb_marketplace_metrics

    matrix = [
        [
            "Предмет",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Удержания за продвижение",
            "К перечислению, руб.",
        ],
        ["Футболка Premium", "80", "90", "10", "5000", "100000"],
        ["DEAD", "0", "20", "20", "1000", "5000"],
    ]
    base = '{"title":"T","headers":["A"],"rows":[["1"]]}'
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=105_000.0)
    enriched = enrich_table_json_wb_finance(
        base,
        revenue_total=105_000.0,
        wb_metrics=wb,
        matrix_rows=matrix,
    )
    import json

    payload = json.loads(enriched)
    assert "abc_analysis" in payload
    assert "sku_catalog" in payload
    assert payload["summary"]["business_score"] > 0


def test_dedupe_report_noise_oos() -> None:
    from services.table_wb_finance_ai import _dedupe_report_noise

    raw = "«BOX» через 0 дн. (риск OOS) (риск OOS)"
    assert _dedupe_report_noise(raw) == "«BOX» через 0 дн. (риск OOS)"


def test_build_matrix_problem_zones_ballast_and_illiquid() -> None:
    from services.file_processor import compute_seller_matrix_etl
    from services.table_wb_finance_ai import build_matrix_problem_zones_block

    matrix = [
        [
            "Предмет",
            "Артикул",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
            "Остаток на складе, шт.",
        ],
        ["LEADER", "L-1", "80", "100", "5", "1000", "90000", "10"],
        ["BALLAST", "B-1", "1", "80", "20", "12000", "0", "1"],
        ["ILLIQ", "I-9", "0", "5", "0", "500", "0", "120"],
    ]
    etl = compute_seller_matrix_etl(matrix, revenue_total=90_000.0)
    assert etl is not None
    block = build_matrix_problem_zones_block(etl)
    assert "Балласт" in block
    assert "Неликвид" in block
    assert "LB-1" in block or "BALLAST" in block or "B-1" in block
    assert "I-9" in block or "ILLIQ" in block


def test_ru_more_goods_suffix_plural() -> None:
    from services.table_wb_finance_ai import _ru_more_goods_suffix

    assert _ru_more_goods_suffix(1) == "… и ещё 1 товар"
    assert _ru_more_goods_suffix(3) == "… и ещё 3 товара"
    assert _ru_more_goods_suffix(5) == "… и ещё 5 товаров"
    assert _ru_more_goods_suffix(22) == "… и ещё 22 товара"


def test_business_score_band_thresholds() -> None:
    from services.table_wb_finance_ai import _business_score_band

    assert _business_score_band(3.5)[0] == "🔴"
    assert "КРИТИЧЕСКИЙ" in _business_score_band(3.5)[1]
    assert _business_score_band(6.0)[0] == "🟡"
    assert "НОРМАЛЬНЫЙ" in _business_score_band(6.0)[1]
    assert _business_score_band(8.5)[0] == "🟢"
    assert "ОТЛИЧНЫЙ" in _business_score_band(8.5)[1]


def test_local_report_shows_score_emoji_and_reason() -> None:
    from services.table_wb_finance_ai import (
        WbFinancePromptMetrics,
        build_wb_finance_express_html_local,
    )

    metrics = WbFinancePromptMetrics(
        revenue=10_000.0,
        tax=600.0,
        clear_profit=9_400.0,
        adv_load_pct=10.0,
        buy_ratio_pct=60.0,
        year_forecast=120_000.0,
        profitability_pct=94.0,
        business_score=7.0,
        verdict="Тест",
        fomo_lost_rub=0.0,
        fomo_breakdown=(),
        abc_a_leader_name="WRAPPER",
        abc_a_leader_article="W-1",
        abc_a_leader_buyout=72.0,
        abc_a_leader_margin=5_000.0,
    )
    html = build_wb_finance_express_html_local(metrics, None)
    assert "🟡" in html
    assert "НОРМАЛЬНЫЙ УРОВЕНЬ" in html
    assert "📈" in html or "📉" in html


def test_local_report_includes_problem_zones() -> None:
    from services.file_processor import compute_seller_matrix_etl
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [
        [
            "Предмет",
            "Артикул",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
            "Остаток на складе, шт.",
        ],
        ["DEAD", "SKU-C1", "0", "10", "0", "500", "0", "100"],
    ]
    metrics = compute_wb_finance_prompt_metrics(
        10_000.0, None, matrix_rows=matrix, platform="wildberries"
    )
    assert metrics is not None
    assert "Неликвид" in metrics.matrix_problem_zones_block
    html = build_wb_finance_express_html_local(metrics, None)
    assert "Проблемные зоны и скрытые убытки" in html
    assert "Неликвид" in html


def test_local_report_includes_cfo_build_marker() -> None:
    from services.table_wb_finance_ai import (
        WbFinancePromptMetrics,
        build_wb_finance_express_html_local,
    )

    metrics = WbFinancePromptMetrics(
        revenue=10_000.0,
        tax=600.0,
        clear_profit=9_400.0,
        adv_load_pct=10.0,
        buy_ratio_pct=60.0,
        year_forecast=120_000.0,
        profitability_pct=94.0,
        business_score=7.0,
        verdict="Тест",
        fomo_lost_rub=0.0,
        fomo_breakdown=(),
        abc_a_leader_name="WRAPPER",
        abc_a_leader_article="W-1",
        abc_a_leader_buyout=72.0,
        abc_a_leader_margin=5_000.0,
    )
    html = build_wb_finance_express_html_local(metrics, None)
    assert "CFO build cfo-v7" in html
    assert "арт. W-1" in html or "W-1" in html


def test_local_report_zero_buyout_leader_goes_critical() -> None:
    from services.table_wb_finance_ai import (
        WbFinancePromptMetrics,
        build_wb_finance_express_html_local,
    )

    metrics = WbFinancePromptMetrics(
        revenue=100_000.0,
        tax=6_000.0,
        clear_profit=94_000.0,
        adv_load_pct=12.0,
        buy_ratio_pct=55.0,
        year_forecast=1_200_000.0,
        profitability_pct=94.0,
        business_score=7.0,
        verdict="Тест",
        fomo_lost_rub=1_000.0,
        fomo_breakdown=(),
        abc_a_leader_name="DEAD",
        abc_a_leader_article="DEAD-99",
        abc_a_leader_buyout=0.0,
        abc_a_leader_margin=-500.0,
        abc_c_summary="• DEAD (арт. DEAD-99)",
        outsider_name="DEAD",
        outsider_article="DEAD-99",
        outsider_loss=500.0,
        outsider_buyout=0.0,
    )
    html = build_wb_finance_express_html_local(metrics, None)
    assert "КРИТИЧЕСКАЯ ЗОНА" in html
    assert "0.0%" in html
    assert "главный источник убытков" in html.lower()
    assert "Масштабируйте закуп и рекламу на этот SKU" not in html


def test_local_report_high_drr_plan_demands_reduction() -> None:
    from services.table_wb_finance_ai import (
        WbFinancePromptMetrics,
        build_wb_finance_express_html_local,
    )

    metrics = WbFinancePromptMetrics(
        revenue=100_000.0,
        tax=6_000.0,
        clear_profit=94_000.0,
        adv_load_pct=24.5,
        buy_ratio_pct=65.0,
        year_forecast=1_200_000.0,
        profitability_pct=94.0,
        business_score=6.0,
        verdict="Тест",
        fomo_lost_rub=0.0,
        fomo_breakdown=(),
        abc_a_leader_name="WRAPPER",
        abc_a_leader_article="W-1",
        abc_a_leader_buyout=72.0,
        abc_a_leader_margin=50_000.0,
        abc_c_summary="• неликвидов нет",
        outsider_name="—",
        outsider_article="—",
        outsider_loss=0.0,
        outsider_buyout=0.0,
    )
    html = build_wb_finance_express_html_local(metrics, None)
    assert "снизить дрр" in html.lower()
    assert "зафиксируйте дрр" not in html.lower()


def test_local_report_catastrophic_drr_plan_demands_cut() -> None:
    from services.table_wb_finance_ai import (
        WbFinancePromptMetrics,
        build_wb_finance_express_html_local,
    )

    metrics = WbFinancePromptMetrics(
        revenue=100_000.0,
        tax=6_000.0,
        clear_profit=94_000.0,
        adv_load_pct=75.6,
        buy_ratio_pct=65.0,
        year_forecast=1_200_000.0,
        profitability_pct=94.0,
        business_score=4.0,
        verdict="Тест",
        fomo_lost_rub=0.0,
        fomo_breakdown=(),
        abc_a_leader_name="WRAPPER",
        abc_a_leader_article="W-1",
        abc_a_leader_buyout=72.0,
        abc_a_leader_margin=50_000.0,
        abc_c_summary="• неликвидов нет",
        outsider_name="—",
        outsider_article="—",
        outsider_loss=0.0,
        outsider_buyout=0.0,
    )
    html = build_wb_finance_express_html_local(metrics, None)
    assert "катастрофический дрр" in html.lower()
    assert "зафиксируйте дрр" not in html.lower()
