"""Доставка отчёта table_generator в Telegram (график + Excel + кнопки)."""

from __future__ import annotations

import logging
import time

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    InputMediaDocument,
    InputMediaPhoto,
    Message,
)

from platforms.table_mini_app_keyboard import (
    get_table_mini_app_keyboard,
    table_delivery_keyboard,
)
from services.table_chart_types import ChartType
from services.table_generator_pack import TABLE_XLSX_FILENAME, build_table_generator_pack
from services.table_session_cache import TableSession, store_table_session
from services.telegram_safe_text import sanitize_telegram_plain_text

logger = logging.getLogger(__name__)


async def _attach_table_keyboard(
    photo_message: Message,
    active: ChartType,
    report_id: int | None,
) -> None:
    try:
        await photo_message.edit_reply_markup(
            reply_markup=table_delivery_keyboard(active, report_id=report_id),
        )
    except TelegramBadRequest:
        logger.debug("edit_reply_markup for table delivery failed", exc_info=True)


async def send_table_generator_pack(
    message: Message,
    raw_json: str,
    *,
    context_text: str = "",
    report_id: int | None = None,
) -> bool:
    pack = build_table_generator_pack(raw_json, context_text=context_text)
    if pack is None:
        return False

    uid = message.from_user.id
    chat_id = message.chat.id
    xlsx_file = BufferedInputFile(pack.xlsx_bytes, filename=TABLE_XLSX_FILENAME)
    caption = pack.telegram_caption_html
    keyboard = table_delivery_keyboard(pack.chart_type, report_id=report_id)
    mini_app_keyboard = get_table_mini_app_keyboard(report_id)
    photo_msg: Message | None = None

    if pack.chart_png_bytes:
        chart_file = BufferedInputFile(pack.chart_png_bytes, filename="chart.png")
        try:
            sent = await message.answer_media_group(
                [
                    InputMediaPhoto(
                        media=chart_file,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                    ),
                    InputMediaDocument(
                        media=xlsx_file,
                        caption="📥 Excel-отчёт для скачивания",
                    ),
                ]
            )
            if sent:
                photo_msg = sent[0]
                await _attach_table_keyboard(photo_msg, pack.chart_type, report_id)
            return _cache_session(uid, chat_id, pack, photo_msg, context_text, report_id)
        except TelegramBadRequest:
            logger.debug("media_group mixed photo+doc failed, fallback", exc_info=True)
        try:
            photo_msg = await message.answer_photo(
                chart_file,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        except TelegramBadRequest:
            photo_msg = await message.answer(sanitize_telegram_plain_text(caption))
        await message.answer_document(
            xlsx_file,
            caption="📥 <b>Отчет_Нейросеть.xlsx</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=mini_app_keyboard,
        )
        return _cache_session(uid, chat_id, pack, photo_msg, context_text, report_id)

    try:
        await message.answer(
            caption,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    except TelegramBadRequest:
        await message.answer(sanitize_telegram_plain_text(caption))
    await message.answer_document(
        xlsx_file,
        caption="📥 <b>Отчет_Нейросеть.xlsx</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=mini_app_keyboard,
    )
    return True


def _cache_session(
    user_id: int,
    chat_id: int,
    pack,
    photo_msg: Message | None,
    context_text: str,
    report_id: int | None = None,
) -> bool:
    if photo_msg is None:
        return True
    store_table_session(
        TableSession(
            user_id=user_id,
            chat_id=chat_id,
            rows=pack.rows,
            caption_html=pack.telegram_caption_html,
            xlsx_bytes=pack.xlsx_bytes,
            chart_message_id=photo_msg.message_id,
            active_chart=pack.chart_type,
            context_text=context_text,
            created_at=time.time(),
            report_id=report_id,
        )
    )
    return True
