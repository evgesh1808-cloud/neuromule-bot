"""Inline-клавиатуры (aiogram), чтобы не тянуть types в messages.py."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from content import messages as msg


def result_photo_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🪄 Оживить это фото (Видео)", callback_data=msg.CB_RESULT_ANIMATE)],
            [InlineKeyboardButton(text="🔄 Повторить генерацию", callback_data=msg.CB_RESULT_REPEAT_PHOTO)],
            [InlineKeyboardButton(text="📥 Скачать в максимальном качестве — PRO", callback_data=msg.CB_RESULT_HD_PRO)],
        ]
    )


def result_video_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Улучшить тариф", callback_data=msg.CB_RESULT_PREMIUM)],
            [InlineKeyboardButton(text="📂 В мою галерею", callback_data=msg.CB_RESULT_GALLERY)],
        ]
    )


def result_music_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📥 Скачать MP3", callback_data=msg.CB_RESULT_MP3)],
            [InlineKeyboardButton(text="✍️ Изменить текст", callback_data=msg.CB_RESULT_EDIT_LYRICS)],
        ]
    )
