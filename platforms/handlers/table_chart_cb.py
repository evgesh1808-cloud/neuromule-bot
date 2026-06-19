"""Callback: бесплатное переключение типа графика table_generator."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, CallbackQuery, InputMediaPhoto

from content import messages as msg
from platforms.table_mini_app_keyboard import table_delivery_keyboard
from services.table_chart_types import ChartType
from services.table_generator_pack import render_chart_png_bytes
from services.table_session_cache import get_table_session, update_active_chart

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(F.data.startswith(msg.CB_TABLE_CHART_PREFIX))
async def switch_table_chart(callback: CallbackQuery) -> None:
    """Перерисовка matplotlib без OpenRouter — только локальный кэш."""
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

    png, resolved = render_chart_png_bytes(
        session.rows,
        chart_type,
        context_text=session.context_text,
    )
    if not png:
        await callback.answer("Недостаточно числовых данных для графика.", show_alert=True)
        return

    media = InputMediaPhoto(
        media=BufferedInputFile(png, filename="chart.png"),
        caption=session.caption_html,
        parse_mode=ParseMode.HTML,
    )
    try:
        await callback.message.edit_media(
            media=media,
            reply_markup=table_delivery_keyboard(resolved, report_id=session.report_id),
        )
    except TelegramBadRequest:
        logger.debug("edit_media chart switch failed", exc_info=True)
        await callback.answer("Не удалось обновить график.", show_alert=True)
        return

    update_active_chart(callback.from_user.id, resolved)
    await callback.answer("График обновлён")
