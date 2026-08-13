"""Inline-клавиатуры под результатом генерации фото (@chatcom-style)."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from content import messages as msg


def new_result_keyboard(*, task_id: str | None = None) -> InlineKeyboardMarkup:
    """Основная сетка под сгенерированным фото."""
    _ = task_id
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔍 Улучшить",
                    callback_data=msg.CB_RESULT_UPSCALE,
                ),
                InlineKeyboardButton(
                    text="🔄 Повторить",
                    callback_data=msg.CB_RESULT_REPEAT_PHOTO,
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🪄 Оживить (Видео)",
                    callback_data=msg.CB_RESULT_ANIMATE,
                ),
                InlineKeyboardButton(
                    text="📐 Сменить формат",
                    callback_data=msg.CB_RESULT_CHANGE_FORMAT,
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Доработать текстом",
                    callback_data=msg.CB_PHOTO_REFINE,
                ),
            ],
        ]
    )


def result_upscale_submenu_keyboard() -> InlineKeyboardMarkup:
    """Подменю «🔍 Улучшить»: edit_reply_markup → CB_RESULT_GRID_BACK."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔍 Сделать чётче х2 (1 💎)",
                    callback_data=msg.CB_RESULT_UPSCALE_X2,
                )
            ],
            [
                InlineKeyboardButton(
                    text="💎 Максимум х4 (3 💎)",
                    callback_data=msg.CB_RESULT_UPSCALE_X4,
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=msg.CB_RESULT_GRID_BACK,
                )
            ],
        ]
    )
