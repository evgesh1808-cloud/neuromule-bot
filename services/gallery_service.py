"""Кросс-пост в публичный Telegram-канал «Галерея NeuroMule».

Шедевр летит в канал ``settings.gallery_channel_id`` с рубрикатором по
хэштегам (``#gallery_flux``/``#studio_video``/``#radio_suno``) и инлайн-кнопкой
обратного захвата трафика ``🎸 Создать свой шедевр в NeuroMule``.

Все ошибки сети/прав канала гасим через ``except Exception`` — основной
воркер бота не должен ронять при сбое кросс-постинга.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings as app_settings
from content import messages as msg
from services.last_share_media import MediaTaskType, ShareMediaEntry

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)


def gallery_channel_configured() -> bool:
    return bool((app_settings.gallery_channel_id or "").strip())


def _hashtag_for(task_type: MediaTaskType) -> str:
    return msg.GALLERY_HASHTAGS.get(task_type, "#NeuroMule")


def _viral_keyboard() -> InlineKeyboardMarkup:
    """Кнопка обратного захвата трафика под пост в TG-канале."""

    username = (app_settings.telegram_bot_username or "NeuroMule_bot").lstrip("@")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_GALLERY_VIRAL_INVITE_BTN,
                    url=f"https://t.me/{username}?start=gallery",
                )
            ]
        ]
    )


def _build_caption(entry: ShareMediaEntry) -> str:
    hashtag = _hashtag_for(entry.task_type)
    prompt = entry.prompt[:600] or "промпт скрыт автором"
    return (
        f"🎨 <b>Шедевр Галереи NeuroMule 🐎⚡️</b>\n\n"
        f"<i>{prompt}</i>\n\n"
        f"{hashtag} #NeuroMule"
    )


async def post_to_gallery_channel(bot: "Bot", entry: ShareMediaEntry) -> bool:
    """Публикует медиа в TG-канал галереи. Возвращает True при успехе."""

    if not gallery_channel_configured():
        logger.info("gallery: TG channel not configured, skip task=%s", entry.task_id)
        return False

    chat_id = (app_settings.gallery_channel_id or "").strip()
    caption = _build_caption(entry)
    reply_markup = _viral_keyboard()
    media = entry.file_id or entry.media_url
    if not media:
        return False

    try:
        if entry.task_type == "photo":
            await bot.send_photo(
                chat_id,
                photo=media,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        elif entry.task_type in ("video", "animate"):
            await bot.send_video(
                chat_id,
                video=media,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        elif entry.task_type == "music":
            await bot.send_audio(
                chat_id,
                audio=media,
                performer="NeuroMule 🐎",
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        else:
            await bot.send_message(
                chat_id,
                caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        return True
    except TelegramBadRequest as exc:
        logger.warning("gallery: TG post failed task=%s err=%s", entry.task_id, exc)
        return False
    except Exception:
        logger.exception("gallery: TG unexpected fail task=%s", entry.task_id)
        return False


async def post_review_to_channel(bot: "Bot", *, review_text: str, review_id: int) -> bool:
    """Публикует одобренный отзыв в канал с тегом ``#user_reviews``."""

    if not gallery_channel_configured():
        return False

    chat_id = (app_settings.gallery_channel_id or "").strip()
    body = (
        "🗣 <b>Голос пользователя NeuroMule 🐎⚡️</b>\n\n"
        f"{review_text[:2000]}\n\n"
        f"#user_reviews #review_{review_id}"
    )
    try:
        await bot.send_message(
            chat_id, body, parse_mode=ParseMode.HTML, reply_markup=_viral_keyboard()
        )
        return True
    except Exception:
        logger.exception("gallery: review post failed review_id=%s", review_id)
        return False


__all__ = (
    "gallery_channel_configured",
    "post_to_gallery_channel",
    "post_review_to_channel",
)
