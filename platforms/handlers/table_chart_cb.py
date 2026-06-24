"""Callback: переключение типа графика table_generator / WB."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, CallbackQuery, InputMediaPhoto

from content import messages as msg
from platforms.table_mini_app_keyboard import table_delivery_keyboard
from services.repository import fetch_table_report_rows_for_user
from services.table_chart_types import ChartType
from services.table_generator_pack import render_chart_png_bytes
from services.table_session_cache import get_table_session, update_active_chart
from services.table_wb_chart import render_wb_chart_from_rows

logger = logging.getLogger(__name__)

router = Router()

_WB_CHART_TYPES = frozenset({"barh", "bar", "line", "pie"})


def _wb_type_to_chart_type(wb_type: str) -> ChartType:
    key = (wb_type or "barh").strip().lower()
    if key == "pie":
        return ChartType.PIE
    if key == "line":
        return ChartType.LINE
    return ChartType.BAR


def _parse_wb_chart_callback(data: str) -> tuple[str, int] | None:
    """``wb_chart:barh:42`` → (``barh``, 42)."""
    if not data.startswith(msg.CB_WB_CHART_PREFIX):
        return None
    rest = data.removeprefix(msg.CB_WB_CHART_PREFIX)
    parts = rest.split(":", 1)
    if len(parts) != 2:
        return None
    chart_type, report_id_raw = parts[0].strip().lower(), parts[1].strip()
    if chart_type not in _WB_CHART_TYPES:
        return None
    try:
        report_id = int(report_id_raw)
    except ValueError:
        return None
    if report_id <= 0:
        return None
    return chart_type, report_id


async def _edit_chart_media(
    callback: CallbackQuery,
    *,
    png: bytes,
    active: ChartType,
    report_id: int | None,
) -> bool:
    if not callback.message:
        return False
    caption = callback.message.caption or callback.message.html_caption or ""
    media = InputMediaPhoto(
        media=BufferedInputFile(png, filename="chart.png"),
        caption=caption,
        parse_mode=ParseMode.HTML,
    )
    try:
        await callback.message.edit_media(
            media=media,
            reply_markup=table_delivery_keyboard(active, report_id=report_id),
        )
        return True
    except TelegramBadRequest:
        logger.debug("edit_media chart switch failed", exc_info=True)
        return False


@router.callback_query(F.data.startswith(msg.CB_WB_CHART_PREFIX))
async def switch_wb_chart(callback: CallbackQuery) -> None:
    """Перерисовка WB-графика по данным из SQLite ``table_reports``."""
    if not callback.from_user or not callback.message:
        return

    parsed = _parse_wb_chart_callback(callback.data or "")
    if parsed is None:
        await callback.answer("Некорректная кнопка графика.", show_alert=True)
        return

    chart_type, report_id = parsed
    uid = callback.from_user.id

    try:
        loaded = await fetch_table_report_rows_for_user(report_id, uid)
        if loaded is None:
            await callback.answer(
                "⚠️ Отчёт не найден или устарел. Сгенерируйте таблицу заново.",
                show_alert=True,
            )
            return

        rows, _title = loaded
        png = render_wb_chart_from_rows(rows, chart_type=chart_type)
        if not png:
            png_bytes, resolved = render_chart_png_bytes(rows, _wb_type_to_chart_type(chart_type))
            if not png_bytes:
                await callback.answer(
                    "⚠️ Не удалось перестроить этот тип графика для данных отчета",
                    show_alert=True,
                )
                return
            png = png_bytes
            active = resolved
        else:
            active = _wb_type_to_chart_type(chart_type)

        if not await _edit_chart_media(
            callback,
            png=png,
            active=active,
            report_id=report_id,
        ):
            await callback.answer(
                "⚠️ Не удалось перестроить этот тип графика для данных отчета",
                show_alert=True,
            )
            return

        update_active_chart(uid, active)
        await callback.answer("График обновлён ✓")
    except Exception:
        logger.exception("switch_wb_chart failed uid=%s report_id=%s", uid, report_id)
        await callback.answer(
            "⚠️ Не удалось перестроить этот тип графика для данных отчета",
            show_alert=True,
        )


@router.callback_query(F.data.startswith(msg.CB_TABLE_CHART_PREFIX))
async def switch_table_chart_legacy(callback: CallbackQuery) -> None:
    """Fallback: переключение по in-memory сессии (без report_id в callback)."""
    if not callback.from_user or not callback.message:
        return

    chart_key = (callback.data or "").removeprefix(msg.CB_TABLE_CHART_PREFIX).strip().lower()
    try:
        chart_type = ChartType(chart_key)
    except ValueError:
        await callback.answer("Неизвестный тип графика.", show_alert=True)
        return

    session = get_table_session(callback.from_user.id)
    if session is None:
        await callback.answer("Сессия устарела. Сгенерируйте таблицу заново.", show_alert=True)
        return
    if callback.message.message_id != session.chart_message_id:
        await callback.answer("Нажмите кнопку под актуальным графиком.", show_alert=True)
        return

    loaded = await fetch_table_report_rows_for_user(session.report_id, callback.from_user.id)
    if loaded is None:
        await callback.answer(
            "⚠️ Отчёт не найден или устарел. Сгенерируйте таблицу заново.",
            show_alert=True,
        )
        return

    rows, context_text = loaded
    wb_key = {"bar": "barh", "line": "line", "pie": "pie"}.get(chart_type.value, "barh")
    try:
        png = render_wb_chart_from_rows(rows, chart_type=wb_key)
        resolved = chart_type
        if not png:
            png_bytes, resolved = render_chart_png_bytes(
                rows,
                chart_type,
                context_text=context_text,
            )
            if not png_bytes:
                await callback.answer(
                    "⚠️ Не удалось перестроить этот тип графика для данных отчета",
                    show_alert=True,
                )
                return
            png = png_bytes

        if not await _edit_chart_media(
            callback,
            png=png,
            active=resolved,
            report_id=session.report_id,
        ):
            await callback.answer(
                "⚠️ Не удалось перестроить этот тип графика для данных отчета",
                show_alert=True,
            )
            return

        update_active_chart(callback.from_user.id, resolved)
        await callback.answer("График обновлён ✓")
    except Exception:
        logger.exception("switch_table_chart_legacy failed uid=%s", callback.from_user.id)
        await callback.answer(
            "⚠️ Не удалось перестроить этот тип графика для данных отчета",
            show_alert=True,
        )
