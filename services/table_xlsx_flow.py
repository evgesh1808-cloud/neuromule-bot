"""Excel → JSON: fast-path, компактный промпт и DeepSeek fallback."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from config import Settings
from content.chat_prompt import build_system_prompt
from services import conversation as conv
from services import metrics
from services.ai_text import ask_ai_messages
from services.dialog_write_worker import commit_assistant_turn_queued
from services.repository import dialog_append, dialog_pop_last_for_user, get_persistent_memory, insert_table_report
from services.table_json import canonicalize_table_json
from services.table_text_response import extract_table_ai_insights
from services.table_number_parse import safe_float
from services.table_markdown import normalize_table_rows
from services.table_xlsx_preprocess import XlsxPreprocessedTable, preprocess_xlsx_rows
from services.telegram_safe_text import _escape_telegram_html, repair_telegram_html
from services.billing import billing
from services.billing.store import refund_charge
from services.rate_limit_service import allow_request, rollback_last
from services.use_cases.chat_turn import (
    ChatTurnOutcome,
    ChatTurnResult,
    _record_chat_success_billing,
    _record_openrouter_usage,
)

logger = logging.getLogger(__name__)

DEEPSEEK_TABLE_FALLBACK_MODEL = "deepseek/deepseek-chat"
TABLE_FALLBACK_TEMPERATURE = 0.1
XLSX_JSON_PREVIEW_ROWS = 30

MSG_XLSX_FILE_PROCESSING = (
    "📥 <b>Файл получен.</b> Провожу аналитическую обработку данных, "
    "пожалуйста, подождите…"
)

_FAST_PATH_ASSISTANT_FALLBACK = (
    "ℹ️ Технический Fast-Path отчет без текстового анализа ИИ."
)


def _resolve_fast_path_assistant_text(
    table_json: str | None,
    *,
    title: str,
    rows: list[list[str]],
) -> str:
    """
    Текст для ``dialog_messages.content`` в локальном fast-path.

    Никогда не возвращает пустую строку — только канонический JSON или метаданные.
    """
    if table_json and str(table_json).strip():
        return str(table_json).strip()
    try:
        if rows:
            headers = [str(c).strip() for c in rows[0]]
            data_rows = [[str(c).strip() for c in row] for row in rows[1:]]
            if headers and any(headers) and data_rows:
                blob = json.dumps(
                    {
                        "title": title or "Отчёт NeuroMule",
                        "headers": headers,
                        "rows": data_rows,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                canonical = canonicalize_table_json(blob)
                if canonical:
                    return canonical
                return blob
    except Exception:
        logger.debug("fast path assistant JSON snapshot failed", exc_info=True)
    return json.dumps(
        {
            "title": title or "Отчёт NeuroMule",
            "fast_path": True,
            "note": _FAST_PATH_ASSISTANT_FALLBACK,
            "row_count": max(len(rows) - 1, 0),
        },
        ensure_ascii=False,
    )


def marketplace_requires_local_path(
    rows: list[list[str]],
    *,
    title: str = "Отчёт NeuroMule",
) -> tuple[bool, XlsxPreprocessedTable]:
    """
   Если матрица пуста или ``revenue_total == 0`` — OpenRouter не вызываем, только Fast-Path.
    """
    pre = preprocess_xlsx_rows(rows, title=title)
    if not pre.rows:
        return True, pre
    if pre.revenue_total <= 0:
        return True, pre
    return False, pre

_WB_PREVIEW_TOP_N = 5
_NAME_MAX_LEN = 40
_SEPARATOR = "───────────────────"

_NAME_COLUMN_RULES: tuple[tuple[str, ...], ...] = (
    ("наименование",),
    ("предмет",),
    ("артикул", "продавца"),
    ("артикул",),
    ("название",),
)

_PCS_COLUMN_RULES: tuple[tuple[str, ...], ...] = (
    ("выкупили", "шт"),
    ("выкупили",),
    ("заказано", "шт"),
)

_RUB_COLUMN_RULES: tuple[tuple[str, ...], ...] = (
    ("перечислению", "товар"),
    ("перечислению", "руб"),
    ("перечислению",),
    ("сумма", "заказов", "комиссия"),
    ("выручка",),
    ("заработок",),
    ("доход",),
)

_MONTH_HINTS: tuple[tuple[str, str], ...] = (
    ("январ", "Январь"),
    ("феврал", "Февраль"),
    ("март", "Март"),
    ("апрел", "Апрель"),
    ("май", "Май"),
    ("июн", "Июнь"),
    ("июл", "Июль"),
    ("август", "Август"),
    ("сентябр", "Сентябрь"),
    ("октябр", "Октябрь"),
    ("ноябр", "Ноябрь"),
    ("декабр", "Декабрь"),
)


@dataclass(frozen=True)
class WbPreviewProduct:
    name: str
    pcs: float
    rub: float


def _match_wb_column(headers: list[str], rules: tuple[tuple[str, ...], ...]) -> int | None:
    lowered = [h.lower() for h in headers]
    for patterns in rules:
        for idx, header in enumerate(lowered):
            if not header:
                continue
            if all(part in header for part in patterns):
                return idx
    return None


def _truncate_product_name(name: str, *, max_len: int = _NAME_MAX_LEN) -> str:
    clean = re.sub(r"\s+", " ", (name or "").strip())
    if len(clean) <= max_len:
        return clean or "—"
    return clean[: max_len - 1].rstrip() + "…"


def _fmt_rub(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


def _fmt_pcs(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:,.1f}".replace(",", " ")


def infer_wb_report_period(title: str) -> str:
    """Пытается вытащить месяц из имени файла / заголовка отчёта."""
    low = (title or "").lower().replace("_", " ").replace("-", " ")
    for stem, label in _MONTH_HINTS:
        if stem in low:
            return label
    clean = (title or "").strip()
    return clean[:48] if clean else "отчётный период"


def aggregate_wb_preview_products(rows: list[list[str]]) -> list[WbPreviewProduct] | None:
    """
    Группирует строки WB по наименованию, суммирует штуки и выручку.
    """
    matrix = normalize_table_rows(rows)
    if len(matrix) < 2:
        return None

    headers = matrix[0]
    name_col = _match_wb_column(headers, _NAME_COLUMN_RULES)
    pcs_col = _match_wb_column(headers, _PCS_COLUMN_RULES)
    rub_col = _match_wb_column(headers, _RUB_COLUMN_RULES)
    if name_col is None or (pcs_col is None and rub_col is None):
        return None

    grouped_pcs: dict[str, float] = defaultdict(float)
    grouped_rub: dict[str, float] = defaultdict(float)

    for row in matrix[1:]:
        name = (row[name_col] if name_col < len(row) else "").strip()
        if not name or name.lower().startswith("итого"):
            continue
        pcs_raw = row[pcs_col] if pcs_col is not None and pcs_col < len(row) else ""
        rub_raw = row[rub_col] if rub_col is not None and rub_col < len(row) else ""
        pcs_val = safe_float(pcs_raw) if pcs_col is not None else 0.0
        rub_val = safe_float(rub_raw) if rub_col is not None else 0.0
        if pcs_val == 0.0 and rub_val == 0.0:
            continue
        grouped_pcs[name] += pcs_val
        grouped_rub[name] += rub_val

    if not grouped_pcs and not grouped_rub:
        return None

    names = set(grouped_pcs) | set(grouped_rub)
    products = [
        WbPreviewProduct(
            name=name,
            pcs=grouped_pcs.get(name, 0.0),
            rub=grouped_rub.get(name, 0.0),
        )
        for name in names
    ]
    products.sort(key=lambda p: (p.rub, p.pcs), reverse=True)
    return products


def build_wb_telegram_preview_html(
    rows: list[list[str]],
    *,
    title: str = "Отчёт NeuroMule",
    total_rub_override: float | None = None,
) -> str | None:
    """
    Премиальное emoji-превью WB для Telegram (без pipe-таблиц и ``│``).

    Возвращает ``None``, если структура не похожа на отчёт Wildberries.
    """
    products = aggregate_wb_preview_products(rows)
    if not products:
        return None

    matrix = normalize_table_rows(rows)
    headers = matrix[0] if matrix else []
    total_pcs = sum(p.pcs for p in products)
    total_rub = sum(p.rub for p in products)
    if total_rub_override is not None and total_rub_override > 0:
        total_rub = total_rub_override
    period = infer_wb_report_period(title)
    ncols = len([h for h in headers if str(h).strip()])

    lines: list[str] = [
        "📊 <b>ОТЧЕТ ПО ПРОДАЖАМ WILDBERRIES</b>",
        f"📆 <b>Период:</b> {_escape_telegram_html(period)}",
        _SEPARATOR,
        f"💰 <b>ОБЩАЯ ВЫРУЧКА:</b> {_fmt_rub(total_rub)} руб.",
        f"📦 <b>ВСЕГО ВЫКУПЛЕНО:</b> {_fmt_pcs(total_pcs)} шт.",
        _SEPARATOR,
        "🔝 <b>Топ-5 товаров по выручке:</b>",
        "",
    ]

    for idx, product in enumerate(products[:_WB_PREVIEW_TOP_N], start=1):
        safe_name = _escape_telegram_html(_truncate_product_name(product.name))
        lines.append(f"{idx}. 🏷️ <b>{safe_name}</b>")
        lines.append(f"   • Выкуплено: <code>{_fmt_pcs(product.pcs)} шт.</code>")
        lines.append(f"   • К перечислению: <code>{_fmt_rub(product.rub)} руб.</code>")
        lines.append("")

    col_hint = f"{ncols}+" if ncols >= 10 else str(max(ncols, 1))
    lines.extend(
        [
            _SEPARATOR,
            (
                f"🗂️ <i>Полный отчет со всеми {col_hint} колонками и интерактивные "
                "графики доступны в прикрепленном файле Excel и внутри WebApp!</i>"
            ),
        ]
    )
    return repair_telegram_html("\n".join(lines))


def resolve_telegram_table_preview_html(
    rows: list[list[str]],
    *,
    title: str | None = None,
) -> str:
    """WB emoji-превью или fallback на классическую таблицу (для не-WB данных)."""
    wb_html = build_wb_telegram_preview_html(rows, title=title or "Отчёт NeuroMule")
    if wb_html:
        return wb_html
    from services.table_generator_pack import markdown_table_to_telegram_caption
    from services.table_xlsx_preprocess import pick_telegram_preview_rows

    preview_rows = pick_telegram_preview_rows(rows)
    return markdown_table_to_telegram_caption(preview_rows, title=title)


async def run_xlsx_fast_path_turn(
    settings: Settings,
    user_id: int,
    rows: list[list[str]],
    *,
    file_name: str,
    title: str,
    table_subrole: str | None = None,
    prebuilt_worker: object | None = None,
    skip_billing: bool = False,
    column_structure_warning: bool = False,
) -> ChatTurnResult:
    """
    Локальный fast-path: Excel → pack без OpenRouter (100% маржа).

    ``skip_billing=True`` — после refund ИИ-пайплайна, без повторного списания.
    """
    from services.billing.chat_pipeline import plan_text_chat
    from services.table_processing_worker import (
        run_table_processing_from_rows_async,
    )
    from services.table_subrole_types import DEFAULT_TABLE_SUBROLE, normalize_table_subrole

    subrole = normalize_table_subrole(table_subrole or DEFAULT_TABLE_SUBROLE)
    if not rows or not any(str(c).strip() for row in rows for c in row):
        return ChatTurnResult(outcome=ChatTurnOutcome.EMPTY_INPUT)

    if not await allow_request(settings, user_id, settings.chat_rate_limit_per_minute):
        return ChatTurnResult(outcome=ChatTurnOutcome.RATE_LIMITED)

    charge_id: str | None = None
    billing_result = None
    if skip_billing:
        user = await billing.load_user(user_id)
        plan = plan_text_chat(user, "table_generator")
        effective_role = "table_generator"
    else:
        billing_result = await billing.resolve_and_charge_text_chat(user_id, "table_generator")
        plan = billing_result.plan
        charge_id = billing_result.charge_id
        effective_role = billing_result.effective_role_id

        if plan.blocked:
            await rollback_last(settings, user_id)
            if plan.block_reason in ("expert_role_requires_paid_tariff", "role_requires_smart_tariff"):
                return ChatTurnResult(outcome=ChatTurnOutcome.ROLE_NOT_ALLOWED)
            return ChatTurnResult(outcome=ChatTurnOutcome.INSUFFICIENT_BALANCE)

    preprocessed = preprocess_xlsx_rows(rows, title=title)
    if not preprocessed.rows:
        if charge_id:
            await refund_charge(charge_id)
        await rollback_last(settings, user_id)
        return ChatTurnResult(outcome=ChatTurnOutcome.AI_FAILED)

    worker = prebuilt_worker
    if worker is None:
        worker = await run_table_processing_from_rows_async(
            preprocessed.rows,
            subrole,
            title=preprocessed.title,
        )
    if worker is None:
        if charge_id:
            await refund_charge(charge_id)
        await rollback_last(settings, user_id)
        return ChatTurnResult(outcome=ChatTurnOutcome.AI_FAILED)

    table_json = rows_to_canonical_table_json(
        worker.rows,
        title=worker.title,
        summary=preprocessed.summary,
    )
    assistant_dialog_text = _resolve_fast_path_assistant_text(
        table_json,
        title=worker.title,
        rows=worker.rows,
    )
    if not table_json:
        table_json = assistant_dialog_text

    dialog_text = f"[📊 Excel {file_name}]"
    await dialog_append(user_id, "user", dialog_text)
    await commit_assistant_turn_queued(user_id, assistant_dialog_text, settings.dialog_prune_keep)
    report_id = await insert_table_report(user_id, table_json)
    conv.schedule_memory_refresh(settings, user_id)
    metrics.incr("table.xlsx.fast_path", {"outcome": "success", "degraded": str(skip_billing).lower()})
    if not skip_billing:
        _record_chat_success_billing(
            role=effective_role,
            energy_cost=plan.energy_cost,
            crystal_cost=plan.crystal_cost,
        )
    from content.messages import TXT_TABLE_AI_DEGRADATION_NOTICE, TXT_TABLE_COLUMN_PARSE_WARNING

    degradation_notice: str | None = None
    if column_structure_warning:
        degradation_notice = TXT_TABLE_COLUMN_PARSE_WARNING
    elif skip_billing:
        degradation_notice = TXT_TABLE_AI_DEGRADATION_NOTICE

    return ChatTurnResult(
        outcome=ChatTurnOutcome.SUCCESS,
        assistant_message=None,
        user_notice=billing_result.notice if billing_result else None,
        effective_text_role=effective_role,
        table_raw_json=table_json,
        table_report_id=report_id,
        table_worker=worker,
        table_degraded=bool(skip_billing or column_structure_warning),
        table_degradation_notice=degradation_notice,
    )


def rows_to_canonical_table_json(
    rows: list[list[str]],
    *,
    title: str,
    summary: str | None = None,
) -> str | None:
    """Локальная сборка канонического JSON для Mini App / Excel pack."""
    if not rows:
        return None
    headers = [str(c).strip() for c in rows[0]]
    if not headers or not any(headers):
        return None
    data_rows = [[str(c).strip() for c in row] for row in rows[1:]]
    payload: dict[str, object] = {
        "title": title or "Отчёт NeuroMule",
        "headers": headers,
        "rows": data_rows,
    }
    if summary:
        payload["summary"] = summary
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return canonicalize_table_json(blob)


def prepare_xlsx_workbook(
    rows: list[list[str]],
    *,
    title: str,
) -> XlsxPreprocessedTable:
    """Единая точка предобработки сырого Excel перед fast-path / API."""
    return preprocess_xlsx_rows(rows, title=title)


def build_xlsx_api_user_prompt(
    caption: str,
    rows: list[list[str]],
    *,
    title: str,
    max_preview_rows: int = XLSX_JSON_PREVIEW_ROWS,
) -> str:
    """Компактный JSON-слепок (до ``max_preview_rows`` строк) вместо Markdown-таблицы."""
    preprocessed = preprocess_xlsx_rows(rows, title=title)
    matrix = preprocessed.rows
    preview = matrix[: max_preview_rows + 1] if matrix else []
    headers = preview[0] if preview else []
    data_rows = preview[1:] if len(preview) > 1 else []
    snapshot = {
        "title": title,
        "headers": headers,
        "rows": data_rows,
        "truncated": len(matrix) > len(preview),
        "total_rows": max(len(matrix) - 1, 0),
    }
    instruction = (
        f"{caption.strip()}\n\n"
        "Верни СТРОГО один JSON-объект {title, headers, rows} по данным ниже. "
        "Без Markdown, без HTML, без пояснений.\n\n"
        f"Данные Excel (JSON):\n{json.dumps(snapshot, ensure_ascii=False)}"
    )
    return instruction


async def run_table_json_deepseek_fallback(
    settings: Settings,
    user_id: int,
    raw_user_text: str,
    *,
    dialog_user_text: str | None = None,
    http_client: object | None = None,
) -> ChatTurnResult:
    """
    Резервный JSON-запрос без повторного списания (после refund основного хода).

    Безопасен для повторного вызова после ``AI_FAILED`` / ``TABLE_JSON_INVALID``.
    """
    history = (dialog_user_text or raw_user_text or "").strip() or "[📊 Excel]"
    await dialog_append(user_id, "user", history)
    mem = await get_persistent_memory(user_id)
    system = build_system_prompt(settings, mem, "table_generator", premium=True)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": raw_user_text},
    ]

    try:
        completion = await ask_ai_messages(
            settings,
            messages,
            timeout=settings.openrouter_timeout_sec,
            max_context_tokens=settings.chat_max_context_tokens_est,
            char_per_token=settings.chat_char_per_token_est,
            http_client=http_client,
            models=[DEEPSEEK_TABLE_FALLBACK_MODEL],
            max_tokens=settings.openrouter_table_max_output_tokens,
            text_role="table_generator",
            temperature=TABLE_FALLBACK_TEMPERATURE,
        )
    except Exception:
        logger.exception("table_json_deepseek_fallback failed user_id=%s", user_id)
        await dialog_pop_last_for_user(user_id)
        return ChatTurnResult(outcome=ChatTurnOutcome.AI_FAILED)

    content = completion.get("content") or ""
    try:
        prompt_tokens = int(completion.get("prompt_tokens") or 0)
        completion_tokens = int(completion.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        prompt_tokens = 0
        completion_tokens = 0

    _record_openrouter_usage(
        user_id=user_id,
        model_id=DEEPSEEK_TABLE_FALLBACK_MODEL,
        role="table_generator",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    metrics.incr("table.xlsx.deepseek_fallback", {"outcome": "called"})

    try:
        table_json = canonicalize_table_json(content)
        if not table_json:
            raise ValueError("deepseek fallback: invalid table JSON")

        ai_insights = extract_table_ai_insights(content)
        await commit_assistant_turn_queued(user_id, table_json, settings.dialog_prune_keep)
        report_id = await insert_table_report(user_id, table_json)
        conv.schedule_memory_refresh(settings, user_id)
        metrics.incr("table.xlsx.deepseek_fallback", {"outcome": "success"})
        return ChatTurnResult(
            outcome=ChatTurnOutcome.SUCCESS,
            assistant_message=None,
            effective_text_role="table_generator",
            table_raw_json=table_json,
            table_report_id=report_id,
            table_ai_insights=ai_insights,
        )
    except Exception:
        logger.warning(
            "table_json_deepseek_fallback invalid JSON user_id=%s raw=%s",
            user_id,
            content[:500],
            exc_info=True,
        )
        metrics.incr("table.xlsx.deepseek_fallback", {"outcome": "invalid_json"})
        await dialog_pop_last_for_user(user_id)
        return ChatTurnResult(outcome=ChatTurnOutcome.TABLE_JSON_INVALID)
