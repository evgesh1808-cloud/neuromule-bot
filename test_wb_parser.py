#!/usr/bin/env python3
"""Локальная диагностика WB Excel → payload для OpenRouter.

Запуск из корня проекта:
    python test_wb_parser.py

Положите свой отчёт WB как test_wb.xlsx рядом со скриптом.
Если файла нет — будет создан синтетический образец с колонками Бренд/Предмет/Артикул.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

XLSX_PATH = ROOT / "test_wb.xlsx"


def write_sample_wb_xlsx(path: Path) -> None:
    """Синтетический отчёт WB (структура как в реальном xlsx с шапкой поставщика)."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Детализация"
    for row in (
        ["", "", "", "", "", "", "", "", "", ""],
        ["Отчёт по данным поставщика ООО Тест-Селлер", "", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", "", ""],
        [
            "Бренд",
            "Предмет",
            "Артикул",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
            "Вознаграждение",
            "Остаток на складе, шт.",
        ],
        ["ACME", "Платье летнее", "SKU-A1", 70, 90, 5, 3500, 100000, 8000, 30],
        ["ACME", "Платье вечернее", "SKU-A2", 14, 20, 2, 800, 15000, 2000, 6],
        ["NOVA", "Сарафан", "SKU-B1", 25, 30, 1, 1200, 28000, 3500, 12],
        ["DEAD", "Неликвид", "SKU-C1", 0, 10, 0, 500, 0, 400, 100],
        ["ИТОГО", "", "", 109, 150, 8, 6000, 143000, 13900, ""],
    ):
        ws.append(row)
    wb.save(path)


def _sku_dict(sku: Any) -> dict[str, Any]:
    return {
        "name": sku.name,
        "article_id": sku.article_id,
        "revenue": sku.revenue,
        "net_profit": sku.net_profit,
        "buyout_pct": sku.buyout_pct,
        "abc_group": getattr(sku, "abc_group", None),
    }


def run_pipeline(xlsx_path: Path) -> dict[str, Any]:
    from services.file_processor import compute_seller_matrix_etl, read_xlsx_rows_from_path
    from services.table_wb_finance_ai import (
        build_wb_finance_openrouter_prompt_pair,
        build_wb_marketplace_finance_payload_dict,
        compute_wb_finance_prompt_metrics,
        resolve_wb_metrics_for_rows,
    )
    from services.table_xlsx_preprocess import preprocess_xlsx_rows

    raw_rows = read_xlsx_rows_from_path(xlsx_path)
    pre = preprocess_xlsx_rows(raw_rows, title="Тест WB")
    matrix = pre.rows
    revenue = float(pre.revenue_total or 0.0)

    wb_metrics = resolve_wb_metrics_for_rows(matrix, revenue, platform="wildberries")
    prompt_metrics = compute_wb_finance_prompt_metrics(
        revenue,
        wb_metrics,
        matrix_rows=matrix,
        platform="wildberries",
    )
    if prompt_metrics is None:
        raise RuntimeError("compute_wb_finance_prompt_metrics вернул None — проверьте выручку в файле")

    etl = compute_seller_matrix_etl(matrix, revenue_total=revenue, platform="wildberries")
    payload = build_wb_marketplace_finance_payload_dict(prompt_metrics, wb_metrics)
    pair = build_wb_finance_openrouter_prompt_pair(
        matrix,
        revenue_total=revenue,
        wb_metrics=wb_metrics,
    )

    return {
        "source": str(xlsx_path),
        "preprocess": {
            "title": pre.title,
            "summary": pre.summary,
            "row_count": max(0, len(matrix) - 1),
            "revenue_total": revenue,
            "headers": matrix[0] if matrix else [],
        },
        "matrix_etl": {
            "abc_group_a": [_sku_dict(s) for s in (etl.abc_group_a if etl else ())],
            "abc_group_c": [_sku_dict(s) for s in (etl.abc_group_c if etl else ())],
            "logistics_fomo_rub": etl.logistics_fomo_rub if etl else 0.0,
            "sku_catalog_lines": [s.catalog_line() for s in (etl.sku_catalog if etl else ())],
        },
        "openrouter_user_payload": payload,
        "openrouter_system_prompt_chars": len(pair[0]) if pair else 0,
    }


def print_checks(result: dict[str, Any]) -> None:
    payload = result.get("openrouter_user_payload") or {}
    etl = result.get("matrix_etl") or {}
    pre = result.get("preprocess") or {}

    revenue = float(payload.get("revenue_rub", 0))
    tax = float(payload.get("tax_usn_6pct_rub", 0))
    profit = float(payload.get("clear_profit_rub", 0))
    pre_rev = float(pre.get("revenue_total", 0))

    expected_tax = round(revenue * 0.06, 2)

    print("\n=== Проверки ===")
    print(f"Выручка preprocess: {pre_rev:,.2f} руб. | payload revenue_rub: {revenue:,.2f} руб.")
    rev_ok = abs(pre_rev - revenue) < 0.01 if pre_rev > 0 else revenue > 0
    print(f"  Согласованность выручки: {'OK' if rev_ok else 'FAIL'}")

    tax_ok = tax == expected_tax
    print(f"Налог УСН 6%: {tax:,.2f} руб. (ожид. {expected_tax:,.2f}) — {'OK' if tax_ok else 'FAIL'}")

    print(f"Чистая прибыль: {profit:,.2f} руб. (налог уже вычтен из выручки в ETL)")

    group_a = etl.get("abc_group_a") or []
    group_c = etl.get("abc_group_c") or []
    print(f"\nГруппа A: {len(group_a)} SKU — {'OK' if group_a else 'ПУСТО'}")
    for sku in group_a:
        print(f"  • {sku['name']} / арт. {sku['article_id']} — маржа {sku['net_profit']:,.0f} руб.")

    print(f"Группа C: {len(group_c)} SKU — {'OK' if group_c else 'ПУСТО'}")
    for sku in group_c:
        print(f"  • {sku['name']} / арт. {sku['article_id']} — маржа {sku['net_profit']:,.0f} руб.")

    catalog = payload.get("sku_catalog") or []
    print(f"\nsku_catalog ({len(catalog)} позиций, ключ = Бренд + Артикул):")
    articles: set[str] = set()
    for item in catalog:
        if not isinstance(item, dict):
            continue
        art = str(item.get("article_id", ""))
        articles.add(art)
        print(
            f"  • {item.get('name')} | арт. {art} | "
            f"выкуп {item.get('buyout_pct')}% | ABC {item.get('abc_group')}"
        )
    dup_articles = len(catalog) - len(articles)
    print(f"  Уникальных артикулов: {len(articles)} — {'OK' if dup_articles == 0 else 'дубли!'}")

    buyout = payload.get("buyout_coef_pct", 0)
    fomo = float(payload.get("fomo_lost_rub", 0))
    fomo_bd = payload.get("fomo_breakdown") or []
    log_fomo = float(payload.get("logistics_fomo_rub", 0))
    print(f"\nВыкуп (buyout_coef_pct): {buyout}%")
    print(f"Упущенная выгода fomo_lost_rub: {fomo:,.2f} руб.")
    print(f"Логистика невыкупов logistics_fomo_rub: {log_fomo:,.2f} руб.")
    print(f"fomo_breakdown ({len(fomo_bd)} пунктов):")
    for line in fomo_bd[:5]:
        print(f"  — {line}")
    metrics_ok = buyout > 0 and (fomo > 0 or log_fomo > 0)
    print(f"Метрики выкупа/FOMO: {'OK' if metrics_ok else 'проверьте колонки доставок/логистики'}")


def main() -> None:
    if not XLSX_PATH.exists():
        print(f"{XLSX_PATH.name} не найден — создаю синтетический образец WB...")
        write_sample_wb_xlsx(XLSX_PATH)
        print(f"Создан: {XLSX_PATH}")
        print("Замените файл своим реальным отчётом WB и запустите снова.\n")

    result = run_pipeline(XLSX_PATH)
    print_checks(result)
    print("\n=== JSON для OpenRouter (openrouter_user_payload) ===")
    print(json.dumps(result["openrouter_user_payload"], ensure_ascii=False, indent=2))
    print("\n=== MPSTATS JSON (prepare_wb_data_for_ai) ===")
    from services.table_wb_finance_ai import prepare_wb_data_for_ai

    print(prepare_wb_data_for_ai(XLSX_PATH))


if __name__ == "__main__":
    main()
