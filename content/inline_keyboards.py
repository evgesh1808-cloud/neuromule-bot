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
    include_set_city: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура конструктора режима «Блогер» под последним постом.

    ``post_id`` связывает кнопки с черновиком в in-memory кэше (``blogger_post_cache``).
    ``include_set_city`` — кнопка смены локации (только при дефолтном городе).
    """
    builder = InlineKeyboardBuilder()
    if include_hashtags:
        builder.row(
            InlineKeyboardButton(
                text="#️⃣ Подобрать хэштеги",
                callback_data=f"{msg.CB_BLOG_HASH_PREFIX}{post_id}",
            )
        )
    if include_set_city:
        builder.row(
            InlineKeyboardButton(
                text=msg.BTN_BLOGGER_SET_CITY,
                callback_data=f"{msg.CB_BLOGGER_SET_CITY_PREFIX}{post_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="🔄 Адаптировать",
            callback_data=f"{msg.CB_BLOG_ADAPT_PREFIX}{post_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🎨 Создать AI-обложку",
            callback_data=f"{msg.CB_BLOGGER_COVER_PREFIX}{post_id}",
        )
    )
    return builder.as_markup()


def get_blogger_cover_options_keyboard(post_id: str) -> InlineKeyboardMarkup:
    """Подменю формата AI-обложки при нажатии «🎨 Создать AI-обложку»."""
    pid = (post_id or "").strip()
    prefix = msg.CB_COVER_GENERATE_PREFIX
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=msg.BTN_BLOGGER_COVER_MODE_NONE,
            callback_data=f"{prefix}{msg.COVER_MODE_NONE}:{pid}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=msg.BTN_BLOGGER_COVER_MODE_FACE,
            callback_data=f"{prefix}{msg.COVER_MODE_FACE}:{pid}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=msg.BTN_BLOGGER_COVER_MODE_OBJECT,
            callback_data=f"{prefix}{msg.COVER_MODE_OBJECT}:{pid}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=msg.BTN_BLOGGER_COVER_BACK,
            callback_data=f"{msg.CB_BLOG_BACK_PREFIX}{pid}",
        )
    )
    return builder.as_markup()


def get_blogger_cover_face_reuse_keyboard(post_id: str) -> InlineKeyboardMarkup:
    """Выбор: сохранённое фото лица или загрузка нового."""
    pid = (post_id or "").strip()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.BTN_BLOGGER_FACE_USE_SAVED,
                    callback_data=f"{msg.CB_BLOGGER_FACE_USE_PREFIX}{pid}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=msg.BTN_BLOGGER_FACE_UPLOAD_NEW,
                    callback_data=f"{msg.CB_BLOGGER_FACE_NEW_PREFIX}{pid}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=msg.BTN_BLOGGER_COVER_BACK,
                    callback_data=f"{msg.CB_BLOG_BACK_PREFIX}{pid}",
                )
            ],
        ]
    )


def get_blogger_cover_face_keyboard(post_id: str) -> InlineKeyboardMarkup:
    """Legacy: загрузить фото лица или сгенерировать обложку без лица."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📸 Загрузить фото",
                    callback_data=f"{msg.CB_BLOGGER_COVER_UPLOAD_FACE_PREFIX}{post_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🖼️ Создать без фото",
                    callback_data=f"{msg.CB_BLOGGER_COVER_NO_FACE_PREFIX}{post_id}",
                )
            ],
        ]
    )


def get_blogger_adapt_keyboard(post_id: str) -> InlineKeyboardMarkup:
    """Подменю выбора площадки для реформата поста (3 💎)."""
    from services.blogger_adaptation import get_blogger_adapt_keyboard as _build

    return _build(post_id)


get_blogger_adaptation_keyboard = get_blogger_adapt_keyboard


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
