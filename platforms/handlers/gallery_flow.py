"""Защитный шлагбаум + мульти-платформенный кросс-пост шедевров.

Сценарий:

1. Под результатом генерации стоит кнопка ``📢 Поделиться в Галерее`` →
   ``share_to_gallery``.
2. Хэндлер :func:`cb_share_to_gallery` перерисовывает клавиатуру текущего
   сообщения в карточку **подтверждения с гарантией анонимности**. Никаких
   моментальных постов — обязательное двойное согласие.
3. После явного ``✅ Да, опубликовать!`` (``confirm_gallery_publish``) бот
   параллельно постит шедевр в **3 витрины**: Telegram-канал галереи (с
   рубрикатор-хэштегами), VK-группу (фото/видео/аудио), MAX App (видео-поток).
4. ``❌ Отмена`` (``cancel_gallery_publish``) возвращает оригинальную
   клавиатуру результата без публикации.

Все ошибки кросс-постинга глушим через ``except Exception`` внутри
сервисов — основной воркер Telegram-бота не падает, юзер видит честный
``TXT_GALLERY_PUBLISHED_PARTIAL`` со списком провалившихся витрин.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from config import settings as app_settings
from content import messages as msg
from platforms.telegram_notify import safe_send_user_message
from services import (
    gallery_service,
    last_share_media,
    max_app_service,
    vk_gallery_service,
    webapp_gallery,
)
from services.last_share_media import MediaTaskType, ShareMediaEntry

logger = logging.getLogger(__name__)

router = Router(name="gallery_flow")


# ─── keyboards ──────────────────────────────────────────────────────────────


def gallery_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_GALLERY_CONFIRM_BTN,
                    callback_data=msg.CB_GALLERY_CONFIRM,
                )
            ],
            [
                InlineKeyboardButton(
                    text=msg.TXT_GALLERY_CANCEL_BTN,
                    callback_data=msg.CB_GALLERY_CANCEL,
                )
            ],
        ]
    )


def gallery_share_row(task_id: str | None = None) -> list[InlineKeyboardButton]:
    """Стандартный ряд под результат: «Поделиться» + «Переслать другу»."""

    buttons: list[InlineKeyboardButton] = [
        InlineKeyboardButton(
            text=msg.TXT_GALLERY_SHARE_BTN,
            callback_data=msg.CB_SHARE_TO_GALLERY,
        )
    ]
    payload = f"get_media_{task_id}" if task_id else "get_media_last"
    buttons.append(
        InlineKeyboardButton(
            text=msg.TXT_GALLERY_FORWARD_FRIEND_BTN,
            switch_inline_query=payload,
        )
    )
    return buttons


# ─── confirm card with anonymity guarantee ──────────────────────────────────


@router.callback_query(F.data == msg.CB_SHARE_TO_GALLERY)
async def cb_share_to_gallery(callback: CallbackQuery) -> None:
    """Шаг 1: гасим анимацию + рисуем карточку подтверждения с анонимностью."""

    await callback.answer()
    if callback.message is None:
        return

    entry = last_share_media.get_by_user(callback.from_user.id)
    if entry is None:
        await callback.answer(msg.TXT_GALLERY_NOT_FOUND, show_alert=True)
        return

    # Перерисовываем клавиатуру текущего сообщения на confirm-карточку,
    # сам caption/текст не трогаем, чтобы не порвать UX медиа-сообщения.
    try:
        await callback.message.edit_reply_markup(reply_markup=gallery_confirm_keyboard())
    except TelegramBadRequest:
        pass

    # Дополнительно отправляем отдельный HTML-блок с обещанием анонимности.
    try:
        await callback.message.answer(
            msg.TXT_GALLERY_CONFIRM_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=gallery_confirm_keyboard(),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == msg.CB_GALLERY_CANCEL)
async def cb_gallery_cancel(callback: CallbackQuery) -> None:
    await callback.answer(msg.TXT_GALLERY_CANCELLED)
    if callback.message is None:
        return
    try:
        # Возвращаем НЕЙТРАЛЬНЫЙ ряд кнопок (share + forward) под медиа.
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[gallery_share_row()]
            )
        )
    except TelegramBadRequest:
        pass


# ─── confirmed publish ──────────────────────────────────────────────────────


async def _resolve_telegram_direct_url(bot, file_id: str | None) -> str | None:
    """Превращает Telegram ``file_id`` в прямой ``api.telegram.org/file/...``.

    Используется для записи в единую таблицу ``webapp_gallery``: фронт Mini App
    (TG / VK / MAX) откроет этот URL напрямую, без проксирования через бэк.
    Сильно сэкономит трафик и не «жжёт» Telegram rate-limit на bot API.
    """

    if not file_id:
        return None
    try:
        file_obj = await bot.get_file(file_id)
    except Exception:
        logger.warning("gallery: bot.get_file failed file_id=%s", file_id, exc_info=True)
        return None
    file_path = getattr(file_obj, "file_path", None)
    if not file_path:
        return None
    token = getattr(getattr(bot, "token", None), "__str__", lambda: "")()
    if not token:
        # aiogram>=3 хранит токен прямо в bot.token (str). Подстраховка.
        token = str(getattr(bot, "token", "") or "")
    if not token:
        return None
    return f"https://api.telegram.org/file/bot{token}/{file_path}"


async def _persist_to_webapp_gallery(
    entry: ShareMediaEntry, bot
) -> bool:
    """Атомарная запись в единое ядро ``webapp_gallery`` (Mini App backend).

    Стратегия выбора URL:
    1. Сначала пробуем превратить Telegram ``file_id`` в прямой URL — самый
       быстрый и стабильный источник для всех трёх витрин Mini App.
    2. Если ``file_id`` нет / get_file упал — используем оригинальный
       ``media_url`` (Replicate / Suno / Imagen). Он тоже публичный.
    3. Если оба источника пусты — пишем ``False`` без падения.
    """

    media_url = await _resolve_telegram_direct_url(bot, entry.file_id)
    if not media_url:
        media_url = entry.media_url
    if not media_url:
        return False
    return await webapp_gallery.publish_to_gallery(
        task_id=entry.task_id,
        user_id=entry.user_id,
        task_type=entry.task_type,  # type: ignore[arg-type]
        prompt=entry.prompt,
        media_url=media_url,
    )


async def _cross_post(entry: ShareMediaEntry, bot) -> dict[str, bool]:
    """Параллельный кросс-пост в WebApp БД + TG канал / VK / MAX App.

    Запись в ``webapp_gallery`` (единая БД Mini App) идёт строго ПЕРВОЙ —
    она самая дешёвая, локальная и должна выполниться даже если все
    внешние витрины недоступны. После неё уже параллельно стартует
    тройной кросс-пост.
    """

    webapp_ok = await _persist_to_webapp_gallery(entry, bot)

    tg_task = gallery_service.post_to_gallery_channel(bot, entry)
    vk_task = vk_gallery_service.cross_post_to_vk(entry)
    max_task = max_app_service.cross_post_to_max_app(entry)

    tg_ok, vk_ok, max_ok = await asyncio.gather(
        tg_task,
        vk_task,
        max_task,
        return_exceptions=False,
    )

    return {
        "webapp": bool(webapp_ok),
        "telegram": bool(tg_ok),
        "vk": bool(vk_ok),
        "max_app": bool(max_ok),
    }


def _format_result_message(stats: dict[str, bool]) -> str:
    if not any(stats.values()):
        return msg.TXT_GALLERY_PUBLISHED_EMPTY
    if all(stats.values()):
        return msg.TXT_GALLERY_PUBLISHED_OK
    failed = ", ".join(name for name, ok in stats.items() if not ok)
    return msg.TXT_GALLERY_PUBLISHED_PARTIAL.format(failed=failed)


def _moderation_chat_configured() -> bool:
    return int(getattr(app_settings, "gallery_moderation_chat_id", 0) or 0) != 0


def _moderation_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_GALLERY_APPROVE_BTN,
                    callback_data=f"{msg.CB_GALLERY_APPROVE_PREFIX}{task_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=msg.TXT_GALLERY_REJECT_BTN,
                    callback_data=f"{msg.CB_GALLERY_REJECT_PREFIX}{task_id}",
                )
            ],
        ]
    )


async def _send_to_moderation(entry: ShareMediaEntry, bot) -> bool:
    """Дублирует медиа + промпт в админский чат с inline-модерацией.

    Возвращает ``True``, если успешно поставили на премодерацию (модер.
    чат настроен и сообщение отправлено). При ``False`` вызывающая сторона
    деградирует в авто-публикацию (поведение «без премодерации»).
    """

    if not _moderation_chat_configured():
        return False

    chat_id = int(app_settings.gallery_moderation_chat_id)
    header = msg.TXT_GALLERY_MODERATION_HEADER.format(
        task_id=entry.task_id,
        task_type=entry.task_type,
        user_id=entry.user_id,
        prompt=(entry.prompt or "—")[:600],
    )
    kb = _moderation_keyboard(entry.task_id)
    media = entry.file_id or entry.media_url

    try:
        if entry.task_type == "photo" and media:
            await bot.send_photo(
                chat_id, photo=media, caption=header[:1024],
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )
        elif entry.task_type in ("video", "animate") and media:
            await bot.send_video(
                chat_id, video=media, caption=header[:1024],
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )
        elif entry.task_type == "music" and media:
            await bot.send_audio(
                chat_id, audio=media, caption=header[:1024],
                parse_mode=ParseMode.HTML, reply_markup=kb,
                performer="NeuroMule 🐎",
            )
        else:
            await bot.send_message(
                chat_id, header, parse_mode=ParseMode.HTML, reply_markup=kb,
            )
        return True
    except Exception:
        logger.exception(
            "gallery moderation: failed to send task=%s to chat_id=%s",
            entry.task_id,
            chat_id,
        )
        return False


@router.callback_query(F.data == msg.CB_GALLERY_CONFIRM)
async def cb_gallery_confirm(callback: CallbackQuery) -> None:
    """Юзер согласился публиковать. Бот НЕ публикует сразу — отправляет в
    премодерационный чат-админку. Только после ``approve_gal`` запускается
    реальный кросс-пост в WebApp БД, TG-канал, VK и MAX App.

    Если модерационный чат не настроен — деградация в авто-публикацию
    (с пометкой в лог + предупредительным сообщением юзеру)."""

    await callback.answer()
    if callback.message is None:
        return

    entry = last_share_media.get_by_user(callback.from_user.id)
    if entry is None:
        await callback.answer(msg.TXT_GALLERY_NOT_FOUND, show_alert=True)
        return

    # Снимаем confirm-клаву, чтобы юзер не дважды кликал.
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    bot = callback.message.bot

    # Премодерация настроена → отправляем на review, юзеру говорим «ждём».
    if _moderation_chat_configured():
        sent_ok = await _send_to_moderation(entry, bot)
        if sent_ok:
            try:
                await callback.message.answer(
                    msg.TXT_GALLERY_AWAITING_MODERATION, parse_mode=ParseMode.HTML
                )
            except TelegramBadRequest:
                await callback.message.answer(msg.TXT_GALLERY_AWAITING_MODERATION)
            return
        # Иначе — fallthrough в авто-публикацию (модер. чат недоступен).
        logger.warning(
            "gallery: moderation send failed, falling back to auto-publish task=%s",
            entry.task_id,
        )

    # Auto-publish (когда модер. чат не настроен): полный кросс-пост.
    try:
        stats = await _cross_post(entry, bot)
    except Exception:
        logger.exception("gallery: cross-post pipeline crashed task=%s", entry.task_id)
        stats = {"webapp": False, "telegram": False, "vk": False, "max_app": False}

    text = _format_result_message(stats)
    if not _moderation_chat_configured():
        text = msg.TXT_GALLERY_AUTOPUBLISHED_NO_MOD + "\n\n" + text
    try:
        await callback.message.answer(text, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        await callback.message.answer(text)


# ─── Премодерация: approve / reject из админ-чата ──────────────────────────


def _is_moderator_caller(callback: CallbackQuery) -> bool:
    """Гард: премодерация-callback'и принимаются только из настроенного
    модерационного чата. Иначе любой юзер мог бы «одобрить» себе пост."""

    if not _moderation_chat_configured():
        return False
    if callback.message is None or callback.message.chat is None:
        return False
    return int(callback.message.chat.id) == int(app_settings.gallery_moderation_chat_id)


@router.callback_query(F.data.startswith(msg.CB_GALLERY_APPROVE_PREFIX))
async def cb_gallery_approve(callback: CallbackQuery) -> None:
    """Модератор одобрил публикацию → запускаем кросс-пост."""

    await callback.answer()
    if not _is_moderator_caller(callback) or callback.message is None:
        return
    task_id = (callback.data or "").split(":", 1)[1].strip()
    if not task_id:
        return

    entry = last_share_media.get_by_task(task_id)
    if entry is None:
        await callback.message.reply("⚠️ task_id не найден в кэше — публикация устарела.")
        return

    bot = callback.message.bot
    try:
        stats = await _cross_post(entry, bot)
    except Exception:
        logger.exception("gallery approve: cross-post crashed task=%s", task_id)
        stats = {"webapp": False, "telegram": False, "vk": False, "max_app": False}

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    summary = _format_result_message(stats)
    await callback.message.reply("✅ Одобрено и опубликовано.\n\n" + summary)

    # Пуш автору. safe_send_user_message сам разруливает Forbidden / RetryAfter /
    # BadRequest со специализированными лог-уровнями.
    await safe_send_user_message(
        bot,
        entry.user_id,
        msg.TXT_GALLERY_MOD_APPROVED_NOTIFY,
        context="gallery_approve_notify",
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith(msg.CB_GALLERY_REJECT_PREFIX))
async def cb_gallery_reject(callback: CallbackQuery) -> None:
    """Модератор отклонил публикацию → в БД ничего не пишем, юзеру мягкий пуш."""

    await callback.answer()
    if not _is_moderator_caller(callback) or callback.message is None:
        return
    task_id = (callback.data or "").split(":", 1)[1].strip()
    if not task_id:
        return

    entry = last_share_media.get_by_task(task_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.message.reply("❌ Отклонено.")

    if entry is None:
        return
    bot = callback.message.bot
    await safe_send_user_message(
        bot,
        entry.user_id,
        msg.TXT_GALLERY_MOD_REJECTED_NOTIFY,
        context="gallery_reject_notify",
        parse_mode=ParseMode.HTML,
    )


__all__ = (
    "router",
    "gallery_confirm_keyboard",
    "gallery_share_row",
    "MediaTaskType",
)
