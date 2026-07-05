"""Inline-клавиатуры (aiogram), чтобы не тянуть types в messages.py."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from content import messages as msg


def _gallery_share_row(task_id: str | None = None) -> list[InlineKeyboardButton]:
    """Стандартный ряд под медиа-результат: «Поделиться» + «Переслать другу».

    Изолирован тут, чтобы любая result-клавиатура могла его переиспользовать
    без зависимости от ``platforms.handlers.gallery_flow`` (циклы импортов).
    """

    payload = f"get_media_{task_id}" if task_id else "get_media_last"
    return [
        InlineKeyboardButton(
            text=msg.TXT_GALLERY_SHARE_BTN,
            callback_data=msg.CB_SHARE_TO_GALLERY,
        ),
        InlineKeyboardButton(
            text=msg.TXT_GALLERY_FORWARD_FRIEND_BTN,
            switch_inline_query=payload,
        ),
    ]


def result_photo_keyboard(
    task_id: str | None = None,
    *,
    photo_share_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура под сгенерированным фото.

    B2B-правило: ``photo_share_url`` (↪️ Поделиться результатом) — только FREE,
    всегда на **первой** строке. MINI/SMART/ULTRA передают ``photo_share_url=None``.
    Ряд «🚀 Переслать другу в ЛС`` (``switch_inline_query``) — на всех тарифах.
    """
    rows: list[list[InlineKeyboardButton]] = []
    if photo_share_url:
        rows.append(
            [
                InlineKeyboardButton(
                    text=msg.TXT_PHOTO_SHARE_RESULT_BTN,
                    url=photo_share_url,
                )
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="🪄 Оживить это фото (Видео)", callback_data=msg.CB_RESULT_ANIMATE)],
            [InlineKeyboardButton(text="🔄 Повторить генерацию", callback_data=msg.CB_RESULT_REPEAT_PHOTO)],
            [InlineKeyboardButton(text="📥 Скачать в максимальном качестве — PRO", callback_data=msg.CB_RESULT_HD_PRO)],
            _gallery_share_row(task_id),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def result_video_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Тарифы", callback_data=msg.CB_RESULT_PREMIUM)],
            [InlineKeyboardButton(text="📂 В мою галерею", callback_data=msg.CB_RESULT_GALLERY)],
        ]
    )


def result_music_keyboard() -> InlineKeyboardMarkup:
    """Базовая клавиатура под музыкальным результатом (legacy).

    Сохраняется для обратной совместимости — в новом флоу используем
    :func:`result_music_keyboard_pro`, где подключены апсейл-кнопки.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📥 Скачать MP3", callback_data=msg.CB_RESULT_MP3)],
            [InlineKeyboardButton(text="✍️ Изменить текст", callback_data=msg.CB_RESULT_EDIT_LYRICS)],
        ]
    )


def music_studio_keyboard() -> InlineKeyboardMarkup:
    """Главный экран Музыкальной студии NeuroMule с 3 режимами Suno AI."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_MUSIC_MODE_AI_BTN,
                    callback_data=msg.CB_MUSIC_MODE_AI,
                )
            ],
            [
                InlineKeyboardButton(
                    text=msg.TXT_MUSIC_MODE_CUSTOM_BTN,
                    callback_data=msg.CB_MUSIC_MODE_CUSTOM,
                )
            ],
            [
                InlineKeyboardButton(
                    text=msg.TXT_MUSIC_MODE_INSTRUMENTAL_BTN,
                    callback_data=msg.CB_MUSIC_MODE_INSTRUMENTAL,
                )
            ],
        ]
    )


def get_blogger_keyboard(
    post_id: str,
    *,
    include_hashtags: bool = True,
) -> InlineKeyboardMarkup:
    """Клавиатура конструктора режима «Блогер» под последним постом.

    ``post_id`` связывает кнопки с черновиком в in-memory кэше (``blogger_post_cache``).
    """
    builder = InlineKeyboardBuilder()
    if include_hashtags:
        builder.row(
            InlineKeyboardButton(
                text="#️⃣ Подобрать хэштеги",
                callback_data=f"{msg.CB_BLOG_HASH_PREFIX}{post_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="🔄 Адаптировать (Reels/Shorts)",
            callback_data=f"{msg.CB_BLOG_ADAPT_PREFIX}{post_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🎨 Создать AI-обложку",
            callback_data=f"{msg.CB_BLOG_ART_PREFIX}{post_id}",
        )
    )
    return builder.as_markup()


def get_blogger_adapt_keyboard(post_id: str) -> InlineKeyboardMarkup:
    """Подменю выбора площадки для реформата поста (3 💎)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📱 Сценарий Reels/Shorts (3 💎)",
            callback_data=f"{msg.CB_BLOG_RUN_ADAPT_PREFIX}{post_id}:reels",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="💼 Экспертная статья VC.ru (3 💎)",
            callback_data=f"{msg.CB_BLOG_RUN_ADAPT_PREFIX}{post_id}:vc",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🐦 Короткий твит / Хайп (3 💎)",
            callback_data=f"{msg.CB_BLOG_RUN_ADAPT_PREFIX}{post_id}:twitter",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Вернуться назад",
            callback_data=f"{msg.CB_BLOG_BACK_PREFIX}{post_id}",
        )
    )
    return builder.as_markup()


def result_music_keyboard_pro(task_id: str | None = None) -> InlineKeyboardMarkup:
    """PRO-клавиатура апсейла под готовым треком NeuroMule 🐎⚡️.

    4 фишки апсейла + ряд Галереи (Поделиться/Переслать другу).
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎬 Видеоклип для Shorts ➔ 20 💎",
                    callback_data=msg.CB_MUSIC_CLIP,
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Продлить трек (+1 мин) ➔ 15 💎",
                    callback_data=msg.CB_MUSIC_EXTEND,
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎤 Клон голоса ➔ 10 💎",
                    callback_data=msg.CB_MUSIC_VOICE_CLONE,
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Опубликовать на ИИ-Радио",
                    callback_data=msg.CB_MUSIC_PUBLISH,
                )
            ],
            _gallery_share_row(task_id),
        ]
    )
