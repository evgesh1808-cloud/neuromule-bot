"""Онбординг-флоу `/start` для NeuroMule 🐎⚡️.

Сценарий (HTML, parse_mode=HTML, disable_web_page_preview=True):

  1. Пользователь шлёт ``/start`` → бот отправляет экран-заслонку:
     приветствие + ссылки на правовые документы + просьба подписаться
     на канал ``@mulendeeva_ai``. К сообщению крепится одна inline-кнопка
     «✅ Принять условия и Запустить» (``callback_data=CB_CHECK_SUBSCRIPTION``).

  2. По клику кнопки:
     • ``bot.get_chat_member(chat_id=CHANNEL_ID, user_id=...)`` обёрнут в
       try/except от ``TelegramAPIError`` (любой сбой считаем «не подписан»);
     • если статус подписки = ``creator/administrator/member`` → подписан;
     • иначе показываем всплывающее окно
       ``callback.answer("Пожалуйста, подпишитесь на канал! 🛑", show_alert=True)``;
       экран-заслонка остаётся как есть.

  3. Если подписан:
     • ``callback.answer()`` — снимаем «крутилку» с кнопки;
     • ``callback.message.delete()`` (try/except) — стираем старую заслонку;
     • ``bot.send_message(...)`` — отправляем экран «Доступ открыт» с
       дашбордом аккаунта в ``<code>``-блоке и Reply-клавиатурой
       ``main_menu()`` из ``platforms.telegram_keyboards``.

Имя в приветствии работает динамически:
    • ``message.from_user.first_name`` задано → «Привет, {имя}!»;
    • скрыто/пусто                    → «Привет!» (без хвостовых запятой/пробела).

Для пользователей, чей ``id`` входит в ``tuple(settings.admin_ids)``,
к ``<code>``-блоку дашборда добавляется строка
«📱 ИИ-Панель:   В разработке ⚙️ (Скоро в Web App)».
Для обычных пользователей этой строки нет. Источник правды по
администраторам — единый ``config.settings`` (pydantic-settings, ``.env``).
"""

from __future__ import annotations

import html
import logging
import os
from typing import Final

from aiogram import Bot, F, Router, types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from config import settings
from content import messages as msg
from platforms.telegram_keyboards import main_menu
from services.repository import set_user_accepted_terms

logger = logging.getLogger(__name__)

# ─────────────────────────── Конфиг ───────────────────────────────────────

# В продакшене BOT_TOKEN лучше держать в .env / config.settings.
# Здесь объявлен только для standalone-сценариев; при подключении этого
# роутера в существующий Dispatcher значение токена не используется.
BOT_TOKEN: Final[str] = os.getenv("TELEGRAM_BOT_TOKEN", "")

CHANNEL_ID: Final[str] = "@mulendeeva_ai"
CHANNEL_URL: Final[str] = "https://t.me/mulendeeva_ai"

# Список администраторов больше не хардкодится здесь. Источник правды —
# ``config.settings.admin_ids`` (см. ``.env`` / pydantic-settings). В коде
# обращаемся как ``tuple(settings.admin_ids)``, чтобы получать неизменяемый
# снимок на момент рендера экрана.

# Источник истины по callback'ам — content.messages (общий whitelist для
# TosGate / Terms / Channel / Throttling middleware'ов).
CB_CHECK_SUBSCRIPTION: Final[str] = msg.CB_CHECK_SUBSCRIPTION
CB_RECHECK_SUBSCRIPTION: Final[str] = msg.CB_RECHECK_SUBSCRIPTION

OFFER_URL: Final[str] = (
    "https://telegra.ph/Publichnaya-oferta-servisa-NeuroMule-05-20"
)
PRIVACY_URL: Final[str] = (
    "https://telegra.ph/Politika-konfidencialnosti-servisa-NeuroMule-05-20"
)
SUBSCRIPTION_URL: Final[str] = (
    "https://telegra.ph/Usloviya-regulyarnyh-platezhej-i-podpiski-NeuroMule-05-20"
)

# Telegram возвращает "left"/"kicked" для не-подписанных. Любой Telegram-сбой
# приводит к status=None — это тоже считается «не подписан» (нельзя пропустить
# юзера в обход paywall на случайной 5xx-ошибке API).
NOT_SUBSCRIBED_STATUSES: Final[frozenset[str]] = frozenset({"left", "kicked"})

WELCOME_ACCEPT_BUTTON_TEXT: Final[str] = "✅ Принять условия и Запустить"
OPEN_CHANNEL_BUTTON_TEXT: Final[str] = "📢 Перейти в канал"
RECHECK_BUTTON_TEXT: Final[str] = "✅ Я подписался(ась)"

# Тосты для негативных веток. Первое нажатие → НЕ показываем модальный
# alert (UX-плохо), вместо этого тихо меняем клавиатуру на «Перейти в канал
# + Я подписался(ась)». Повторное нажатие («Я подписался») при оставшейся
# неудаче → модальный alert, потому что юзер уже видит обе кнопки.
NOT_SUBSCRIBED_TOAST_FIRST: Final[str] = (
    "Подпишись на канал и нажми «Я подписался(ась)» 👇"
)
NOT_SUBSCRIBED_ALERT_RETRY: Final[str] = (
    "Кажется, ты ещё не подписался(ась). "
    "Подпишись по кнопке выше 👆 и попробуй ещё раз."
)
# Срабатывает, если Telegram отвечает «member list is inaccessible» — это
# означает, что @NeuroMule_bot НЕ является администратором CHANNEL_ID, и
# get_chat_member физически не может проверить статус. Сообщение для юзера
# должно быть нейтральным (не обвинять его), и параллельно мы пишем
# CRITICAL в лог, чтобы админ сразу увидел инцидент конфигурации.
CHANNEL_INACCESSIBLE_ALERT: Final[str] = (
    "🛠 Сервис временно не может проверить подписку. "
    "Попробуй через минуту или напиши в поддержку."
)

# ─────────────────────────── Утилиты ──────────────────────────────────────


def resolve_greeting(user: User | None) -> str:
    """«Привет, {имя}!» или «Привет!» — без хвостовых запятой/пробела.

    ``first_name`` экранируется через ``html.escape``, чтобы экзотические
    символы (`<`, `&`, `"`) не сломали HTML-разметку Telegram. ``last_name``
    игнорируется намеренно: дашборд персональный и краткий.
    """
    first_name = (getattr(user, "first_name", None) or "").strip() if user else ""
    if not first_name:
        return "Привет!"
    return f"Привет, {html.escape(first_name)}!"


# ─────────────────────────── Тексты экранов ───────────────────────────────


def _welcome_gate_text(user: User | None) -> str:
    """Экран-заслонка: TOS + просьба подписаться."""
    greeting = resolve_greeting(user)
    return (
        "🐎⚡️ <b>Добро пожаловать в NeuroMule!</b>\n\n"
        f"{greeting} Перед запуском, пожалуйста, ознакомься с правилами сервиса:\n\n"
        f"📄 <a href=\"{OFFER_URL}\">Публичная оферта</a> — "
        "договор об оказании услуг.\n"
        f"🔒 <a href=\"{PRIVACY_URL}\">Политика конфиденциальности</a> — "
        "правила обработки персональных данных.\n"
        f"💳 <a href=\"{SUBSCRIPTION_URL}\">Условия регулярных платежей</a> — "
        "правила автосписания при оплате тарифов.\n\n"
        "✨ Чтобы <b>снять ограничения и открыть доступ</b>, подпишись на наш "
        f"официальный канал {CHANNEL_ID}. Там мы делимся секретными промптами "
        "и лайфхаками! 👇\n\n"
        "<i>⚠️ Нажимая кнопку проверки, ты подтверждаешь согласие со всеми "
        "тремя документами и даешь согласие на обработку медиафайлов.</i>"
    )


def _welcome_gate_keyboard() -> InlineKeyboardMarkup:
    """Inline-кнопка «✅ Принять условия и Запустить» (один ряд)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=WELCOME_ACCEPT_BUTTON_TEXT,
                    callback_data=CB_CHECK_SUBSCRIPTION,
                )
            ]
        ]
    )


def _not_subscribed_keyboard() -> InlineKeyboardMarkup:
    """UX «recheck» как у топовых ботов (combot, notcoin и т.п.).

    Две строки: URL-кнопка прямо в канал (юзер мгновенно туда уходит) и
    callback ``recheck_subscription`` для повторной проверки. Когда
    клавиатура подменяется через ``edit_reply_markup``, текст сообщения
    остаётся прежним — TOS-заслонка не моргает и не «съезжает» в чате.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=OPEN_CHANNEL_BUTTON_TEXT,
                    url=CHANNEL_URL,
                ),
            ],
            [
                InlineKeyboardButton(
                    text=RECHECK_BUTTON_TEXT,
                    callback_data=CB_RECHECK_SUBSCRIPTION,
                ),
            ],
        ]
    )


def build_dashboard_text(user: User | None) -> str:
    """Экран «Доступ открыт» с дашбордом аккаунта.

    Дашборд НЕ оборачивается в ``<code>``: в Telegram внутри моноширинного
    блока эмодзи (в т.ч. ``⚡️``) отображаются чёрным контуром, а не
    цветными — из-за этого «⚡ Энергия» выглядела иначе, чем ``🐎⚡️`` в
    обычном тексте выше.

    Строка «📱 ИИ-Панель» зависит от двух параметров:

    * ``settings.is_webapp_enabled is True`` →
      ВСЕ пользователи видят «📱 ИИ-Панель:   Доступна по кнопке 👇»
      (фронт готов, юзер открывает Mini App по кнопке).
    * ``settings.is_webapp_enabled is False`` (rollout-режим):
        * админы из ``tuple(settings.admin_ids)`` → видят заглушку-анонс
          «📱 ИИ-Панель:   В разработке ⚙️ (Скоро в Web App)»;
        * обычные пользователи → строки нет вовсе.
    """
    greeting = resolve_greeting(user)
    user_id = int(getattr(user, "id", 0) or 0)

    dashboard_content = (
        "📋 Твой текущий статус: FREE 🎁\n"
        "🔄 Обновление: Ежедневно\n\n"
        "🔮 Бесплатный совет дня по Дизайну Человека!\n"
        "⚡️ Энергия:     30 запросов в ИИ-Ассистенте (Стандарт)\n"
        "📸 Изображения: 3 генерации в Imagen 4\n"
        f"{msg.BTN_STUDIO_MENU}: аналитика и отчёты — в меню чата Telegram."
    )

    return (
        "🎉 <b>Доступ к NeuroMule открыт!</b>\n"
        "Нейроны готовы к твоим идеям 🐎⚡️\n\n"
        f"{greeting} Добро пожаловать в команду <b>NeuroMule</b>.\n\n"
        "📊 <b>АККАУНТ АКТИВИРОВАН:</b>\n"
        f"{dashboard_content}\n\n"
        "Выбирай направление ниже и давай создавать шедевры! 👇"
    )


# ─────────────────────────── Router ───────────────────────────────────────

router = Router(name="start_onboarding")


@router.message(Command("version"))
async def cmd_version(message: Message) -> None:
    """Диагностика деплоя — первый роутер, чтобы не уходило в ИИ-Ассистент."""
    from platforms.build_info import reply_build_version

    await reply_build_version(message)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """`/start` → экран-заслонка с TOS-ссылками и кнопкой проверки подписки."""
    await message.answer(
        text=_welcome_gate_text(message.from_user),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=_welcome_gate_keyboard(),
    )


@router.callback_query(F.data.in_({CB_CHECK_SUBSCRIPTION, CB_RECHECK_SUBSCRIPTION}))
async def check_user_subscription(callback: types.CallbackQuery) -> None:
    """Проверка подписки на канал + принятие TOS + вход.

    Один handler на ДВЕ callback_data:
    * ``CB_CHECK_SUBSCRIPTION`` — первое нажатие («✅ Принять условия и Запустить»),
    * ``CB_RECHECK_SUBSCRIPTION`` — повторное нажатие («✅ Я подписался(ась)»).

    На неудачной первой проверке мы НЕ показываем модальный alert (UX-плохо),
    а тихо подменяем клавиатуру заслонки на «📢 Перейти в канал» + «✅ Я
    подписался(ась)». На неудачной повторной — уже модальный alert, потому
    что обе кнопки и так перед глазами.

    Особый случай — ``TelegramBadRequest: member list is inaccessible``:
    это означает, что @NeuroMule_bot НЕ админ в ``CHANNEL_ID``. Юзер тут не
    при чём; в лог уходит CRITICAL, а юзеру показываем нейтральный alert
    «сервис временно не может проверить подписку».
    """
    user = callback.from_user
    if user is None:
        await callback.answer()
        return

    user_id = user.id
    bot: Bot | None = callback.bot
    is_retry = (callback.data or "") == CB_RECHECK_SUBSCRIPTION

    # 1. Проверка подписки на CHANNEL_ID. Все ошибки логируем, статусы
    #    различаем: "left"/"kicked" → не подписан (нормальный путь);
    #    "member list is inaccessible" → бот не админ канала (миссконфиг
    #    деплоя, отдельная ветка с CRITICAL).
    status: str | None = None
    bot_not_admin = False
    if bot is not None:
        try:
            member = await bot.get_chat_member(
                chat_id=CHANNEL_ID, user_id=user_id
            )
            status = member.status
        except TelegramBadRequest as exc:
            desc = (str(getattr(exc, "message", "")) or str(exc)).lower()
            if "member list is inaccessible" in desc:
                bot_not_admin = True
                logger.critical(
                    "BOT NOT ADMIN of channel=%s — get_chat_member denied; "
                    "fix: add @NeuroMule_bot as admin in Telegram channel "
                    "(any permissions, even all-disabled). user_id=%s",
                    CHANNEL_ID,
                    user_id,
                )
            else:
                logger.exception(
                    "get_chat_member BadRequest channel=%s user_id=%s",
                    CHANNEL_ID,
                    user_id,
                )
        except TelegramAPIError:
            logger.exception(
                "get_chat_member failed: channel=%s user_id=%s",
                CHANNEL_ID,
                user_id,
            )

    # 1a. Ветка «бот не админ канала». Юзеру — alert с нейтральным текстом.
    if bot_not_admin:
        await callback.answer(text=CHANNEL_INACCESSIBLE_ALERT, show_alert=True)
        return

    # 1b. Юзер не подписан (или Telegram молчит). UX «recheck».
    if status is None or status in NOT_SUBSCRIBED_STATUSES:
        if is_retry:
            await callback.answer(
                text=NOT_SUBSCRIBED_ALERT_RETRY,
                show_alert=True,
            )
            return

        # Первое нажатие → меняем клавиатуру на «Перейти в канал + Я подписался».
        # Текст сообщения не трогаем (edit_reply_markup), чтобы экран не моргал.
        await callback.answer(text=NOT_SUBSCRIBED_TOAST_FIRST, show_alert=False)
        if callback.message is not None:
            try:
                await callback.message.edit_reply_markup(
                    reply_markup=_not_subscribed_keyboard()
                )
            except TelegramBadRequest:
                # Сообщение уже в этом состоянии или удалено — это ок.
                pass
            except TelegramAPIError:
                logger.exception(
                    "edit_reply_markup to recheck-state failed user_id=%s",
                    user_id,
                )
        return

    # 2. Подписка ОК → снимаем «крутилку» с кнопки.
    await callback.answer()

    # 3. Атомарно фиксируем согласие с TOS в БД ДО удаления заслонки и
    #    показа дашборда. ``set_user_accepted_terms`` импортирована на уровне
    #    файла (идиоматичный путь — без проброса модуля services в сигнатуру).
    #    ``accepted`` в репозитории объявлена как keyword-only (см.
    #    ``services/repository.py::set_user_accepted_terms``).
    try:
        await set_user_accepted_terms(user_id, accepted=True)
    except Exception:
        logger.critical(
            "check_user_subscription: set_user_accepted_terms FAILED user_id=%s",
            user_id,
            exc_info=True,
        )

    # 4. Стираем сообщение-заслонку.
    target_chat_id: int | None = None
    if callback.message is not None:
        target_chat_id = callback.message.chat.id
        try:
            await callback.message.delete()
        except Exception:
            pass

    if target_chat_id is None:
        target_chat_id = user_id

    # 5. Отправляем экран «Доступ открыт» с Reply-клавиатурой main_menu().
    #    is_admin прокидываем явно — рассчитан один раз из настроек проекта.
    is_admin = user_id in tuple(settings.admin_ids)
    if bot is None:
        logger.error(
            "check_user_subscription: callback.bot is None user_id=%s",
            user_id,
        )
        return
    try:
        await bot.send_message(
            chat_id=target_chat_id,
            text=build_dashboard_text(user),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=main_menu(is_admin=is_admin),
        )
    except TelegramAPIError:
        logger.exception(
            "send main screen failed user_id=%s chat_id=%s",
            user_id,
            target_chat_id,
        )


__all__ = (
    "BOT_TOKEN",
    "CB_CHECK_SUBSCRIPTION",
    "CB_RECHECK_SUBSCRIPTION",
    "CHANNEL_ID",
    "CHANNEL_INACCESSIBLE_ALERT",
    "CHANNEL_URL",
    "NOT_SUBSCRIBED_ALERT_RETRY",
    "NOT_SUBSCRIBED_STATUSES",
    "NOT_SUBSCRIBED_TOAST_FIRST",
    "OFFER_URL",
    "OPEN_CHANNEL_BUTTON_TEXT",
    "PRIVACY_URL",
    "RECHECK_BUTTON_TEXT",
    "SUBSCRIPTION_URL",
    "WELCOME_ACCEPT_BUTTON_TEXT",
    "build_dashboard_text",
    "check_user_subscription",
    "cmd_start",
    "resolve_greeting",
    "router",
)
