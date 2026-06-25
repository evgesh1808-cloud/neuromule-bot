"""Доставка отчёта table_generator в Telegram (график + Excel + кнопки)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, Message

from platforms.table_mini_app_keyboard import (
    get_table_mini_app_keyboard,
    table_delivery_keyboard,
)
from platforms.telegram_chunks import answer_chat_text
from config import settings
from services.table_generator_pack import TABLE_XLSX_FILENAME, TableGeneratorPack, build_table_generator_pack
from services.table_json import parse_table_json_response
from services.table_session_cache import TableSession, store_table_session
from services.telegram_safe_text import _escape_telegram_html, sanitize_telegram_plain_text

logger = logging.getLogger(__name__)

# Telegram: caption у photo/document — макс. 1024; оставляем запас.
_CAPTION_SAFE_MAX = 1020
_DOC_SHORT_CAPTION = "📥 <b>Отчет_Нейросеть.xlsx</b> — полная таблица для Excel и Mini App"

_T = TypeVar("_T")


async def _await_with_flood_retry(awaitable: Callable[[], Awaitable[_T]]) -> _T:
    """Повторяет вызов Telegram API после ``TelegramRetryAfter`` (FloodWait)."""
    while True:
        try:
            return await awaitable()
        except TelegramRetryAfter as exc:
            wait_sec = max(0.1, float(exc.retry_after))
            logger.info("TelegramRetryAfter: sleep %.1fs before retry", wait_sec)
            await asyncio.sleep(wait_sec)


async def _clear_status_message(status: Message | None) -> None:
    """Убирает промежуточное статусное сообщение перед финальной доставкой."""
    if status is None:
        return

    async def _delete() -> None:
        await status.delete()

    try:
        await _await_with_flood_retry(_delete)
    except TelegramBadRequest:
        try:
            await _await_with_flood_retry(
                lambda: status.edit_text("✅ <b>Готово</b>", parse_mode=ParseMode.HTML)
            )
        except Exception:
            logger.debug("status message cleanup failed", exc_info=True)
    except Exception:
        logger.debug("status message delete failed", exc_info=True)


def _chart_short_caption(report_title: str) -> str:
    title = _escape_telegram_html((report_title or "отчёт").strip()[:80])
    return (
        f"📊 <b>Визуальный анализ продаж WB: {title}</b>\n"
        f"<i>Инфографика трендов и выручки по ТОП-7 предметам</i>"
    )


async def _send_table_text(message: Message, table_html: str) -> None:
    """Длинная таблица отдельным текстовым сообщением (без лимита caption 1024)."""
    try:
        await _await_with_flood_retry(
            lambda: answer_chat_text(message, table_html, settings)
        )
    except Exception:
        logger.debug("answer_chat_text failed, plain fallback", exc_info=True)
        await _await_with_flood_retry(
            lambda: message.answer(sanitize_telegram_plain_text(table_html))
        )


async def _send_excel_document(
    message: Message,
    xlsx_file: BufferedInputFile,
    *,
    caption: str,
    reply_markup,
) -> None:
    try:
        await _await_with_flood_retry(
            lambda: message.answer_document(
                xlsx_file,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        )
    except TelegramBadRequest:
        logger.debug("document HTML caption failed, plain fallback", exc_info=True)
        await _await_with_flood_retry(
            lambda: message.answer_document(
                xlsx_file,
                caption=sanitize_telegram_plain_text(caption),
                reply_markup=reply_markup,
            )
        )


async def send_table_generator_pack(
    message: Message,
    raw_json: str,
    *,
    context_text: str = "",
    report_id: int | None = None,
    status_message: Message | None = None,
    ai_insights: str | None = None,
    table_subrole: str | None = None,
    table_worker: object | None = None,
    seo_xlsx_bytes: bytes | None = None,
    degradation_notice: str | None = None,
    audit_platform: str | None = None,
) -> bool:
    """
    Двухэтапная доставка: (1) график с коротким caption, (2) таблица + Excel + WebApp.

    Если таблица длиннее 1024 символов — текст отдельным сообщением, Excel — следом.
    """
    pack: TableGeneratorPack | None
    if table_worker is not None:
        worker = table_worker
        pack = TableGeneratorPack(
            rows=worker.rows,
            telegram_caption_html=worker.telegram_caption_html,
            xlsx_bytes=seo_xlsx_bytes or worker.xlsx_bytes,
            chart_png_bytes=worker.chart_png_bytes,
            chart_type=worker.chart_type,
            calculated_total=worker.calculated_total,
        )
    else:
        pack = build_table_generator_pack(
            raw_json,
            context_text=context_text,
            ai_insights=ai_insights,
            table_subrole=table_subrole,
        )
        if pack is not None and seo_xlsx_bytes:
            pack = TableGeneratorPack(
                rows=pack.rows,
                telegram_caption_html=pack.telegram_caption_html,
                xlsx_bytes=seo_xlsx_bytes,
                chart_png_bytes=pack.chart_png_bytes,
                chart_type=pack.chart_type,
                calculated_total=pack.calculated_total,
            )
    if pack is None:
        return False

    await _clear_status_message(status_message)

    try:
        payload = parse_table_json_response(raw_json)
    except Exception:
        payload = None
    report_title = (payload.title if payload else None) or "отчёт"

    uid = message.from_user.id
    chat_id = message.chat.id
    xlsx_file = BufferedInputFile(pack.xlsx_bytes, filename=TABLE_XLSX_FILENAME)
    table_caption = pack.telegram_caption_html
    if degradation_notice and table_caption:
        table_caption = f"{table_caption}{degradation_notice}"
    elif degradation_notice:
        table_caption = degradation_notice.lstrip()
    chart_keyboard = table_delivery_keyboard(
        pack.chart_type,
        report_id=report_id,
        platform=audit_platform,
    )
    mini_app_keyboard = get_table_mini_app_keyboard(report_id)
    photo_msg: Message | None = None

    # Шаг 1: график — только короткая подпись, без таблицы.
    if pack.chart_png_bytes:
        chart_file = BufferedInputFile(pack.chart_png_bytes, filename="chart.png")
        short_caption = _chart_short_caption(report_title)
        try:
            photo_msg = await _await_with_flood_retry(
                lambda: message.answer_photo(
                    chart_file,
                    caption=short_caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=chart_keyboard,
                )
            )
        except TelegramBadRequest:
            logger.warning("answer_photo with caption failed, retry without caption", exc_info=True)
            try:
                photo_msg = await _await_with_flood_retry(
                    lambda: message.answer_photo(
                        chart_file,
                        reply_markup=chart_keyboard,
                    )
                )
                await _await_with_flood_retry(
                    lambda: message.answer(short_caption, parse_mode=ParseMode.HTML)
                )
            except TelegramBadRequest:
                photo_msg = await _await_with_flood_retry(
                    lambda: message.answer_photo(chart_file)
                )

    # Шаг 2–3: таблица + Excel (+ WebApp на документе).
    table_fits_caption = len(table_caption) <= _CAPTION_SAFE_MAX

    if table_fits_caption and not pack.chart_png_bytes:
        try:
            await _await_with_flood_retry(
                lambda: message.answer(
                    table_caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=chart_keyboard,
                )
            )
        except TelegramBadRequest:
            await _await_with_flood_retry(
                lambda: message.answer(sanitize_telegram_plain_text(table_caption))
            )
        await _send_excel_document(
            message,
            xlsx_file,
            caption=_DOC_SHORT_CAPTION,
            reply_markup=mini_app_keyboard,
        )
    elif table_fits_caption and pack.chart_png_bytes:
        await _send_excel_document(
            message,
            xlsx_file,
            caption=table_caption,
            reply_markup=mini_app_keyboard,
        )
    else:
        if table_caption.strip():
            await _send_table_text(message, table_caption)
        await _send_excel_document(
            message,
            xlsx_file,
            caption=_DOC_SHORT_CAPTION,
            reply_markup=mini_app_keyboard,
        )

    return _cache_session(uid, chat_id, pack, photo_msg, report_id)


def _cache_session(
    user_id: int,
    chat_id: int,
    pack,
    photo_msg: Message | None,
    report_id: int | None = None,
) -> bool:
    if photo_msg is None or report_id is None or report_id <= 0:
        return True
    store_table_session(
        TableSession(
            user_id=user_id,
            chat_id=chat_id,
            chart_message_id=photo_msg.message_id,
            active_chart=pack.chart_type,
            report_id=report_id,
            created_at=time.time(),
        )
    )
    return True
