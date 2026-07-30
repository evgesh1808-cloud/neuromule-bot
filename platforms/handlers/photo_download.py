"""Кнопка «Скачать без сжатия» — send_document по Telegram file_id из кэша."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import CallbackQuery

from content import messages as msg
from services.photo_dl_callback import CB_DL_FILE_PREFIX, resolve_dl_file_id

logger = logging.getLogger(__name__)

router = Router(name="photo_download")


@router.callback_query(F.data.startswith(CB_DL_FILE_PREFIX))
async def cb_download_uncompressed(callback: CallbackQuery) -> None:
    """Отдать превью как документ, переиспользуя file_id (без трафика на наш сервер)."""
    data = callback.data or ""
    payload = data.removeprefix(CB_DL_FILE_PREFIX).strip()
    uid = callback.from_user.id if callback.from_user else 0

    file_id = resolve_dl_file_id(payload, user_id=uid)
    if not file_id:
        await callback.answer(msg.TXT_DOWNLOAD_UNCOMPRESSED_GONE, show_alert=True)
        return

    if not callback.message:
        await callback.answer(msg.TXT_DOWNLOAD_UNCOMPRESSED_FAIL, show_alert=True)
        return

    try:
        await callback.message.answer_document(
            document=file_id,
            caption=msg.BTN_DOWNLOAD_UNCOMPRESSED,
        )
    except TelegramRetryAfter as exc:
        logger.warning(
            "dl_file retry_after user_id=%s retry_after=%s",
            uid,
            exc.retry_after,
        )
        await callback.answer(
            msg.TXT_DOWNLOAD_UNCOMPRESSED_FAIL,
            show_alert=True,
        )
        return
    except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError) as exc:
        logger.warning("dl_file send_document failed user_id=%s: %s", uid, exc)
        await callback.answer(msg.TXT_DOWNLOAD_UNCOMPRESSED_FAIL, show_alert=True)
        return
    except Exception:
        logger.exception("dl_file unexpected user_id=%s", uid)
        await callback.answer(msg.TXT_DOWNLOAD_UNCOMPRESSED_FAIL, show_alert=True)
        return

    await callback.answer(msg.TXT_DOWNLOAD_UNCOMPRESSED_OK)
