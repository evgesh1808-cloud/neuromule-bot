"""Telegram-интерфейс (aiogram): сборка Dispatcher и точка входа polling."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError

from config import settings
from platforms.handlers import deps, register_all
from platforms.telegram_middleware import (
    ChannelGateMiddleware,
    DailyResetMiddleware,
    InboundUpdateMiddleware,
    TermsGateMiddleware,
)
from platforms.telegram_proxy import resolve_telegram_proxy_url
from platforms.telegram_subscription import ChannelSubscription
from platforms.telegram_throttling import ThrottlingMiddleware
from platforms.telegram_tos_gate import TosGateMiddleware
from platforms.telegram_states import AdminStates, FeedbackStates, UserFlow
from platforms.telegram_utils import HelpInstructionWordFilter, is_admin_user
from services.app_logging import setup_logging
from services.dialog_write_worker import start_dialog_write_worker
from services.last_share_media import clear_expired_cache_loop
from services.hint_session_store import clear_expired_hint_sessions_loop
from services.metrics_http import serve_metrics
from services.openrouter_http import (
    _wait_openrouter_api,
    close_openrouter_http_client,
    init_openrouter_http_client,
    log_openrouter_proxy_configuration,
    probe_openrouter_proxy,
)
from services.repository import init_db
from services.runtime_gc import controlled_gc_loop, setup_optimized_gc

# PR-P Phase 1a · lazy-проводка PostgreSQL-пула. Активируется только
# при ``settings.postgres_dsn != ""``. Импорт обёрнут try/except, чтобы
# отсутствие asyncpg в venv разработчика не ломало telegram-флоу на
# legacy SQLite.
try:
    from services.database import init_postgres_pool
except ImportError:  # pragma: no cover — asyncpg не установлен
    init_postgres_pool = None  # type: ignore[assignment]

# Обратная совместимость для импортов из старого монолита
from platforms.telegram_keyboards import (  # noqa: F401
    cabinet_keyboard,
    channel_gate_markup,
    create_menu,
    main_menu,
    support_faq_keyboard,
    terms_accept_keyboard,
)
from platforms.telegram_utils import (  # noqa: F401
    notify_admins_about_payment,
    send_same_as_instruction_button,
)

logger = logging.getLogger(__name__)

_TELEGRAM_CONNECT_RETRIES = 5
_TELEGRAM_CONNECT_RETRY_SEC = 5.0


async def _setup_studio_menu_button(bot: Bot) -> None:
    """Обёртка для обратной совместимости — см. ``platforms.telegram_studio``."""
    from platforms.telegram_studio import setup_studio_menu_button

    await setup_studio_menu_button(bot)


def _log_build_identity() -> None:
    """В логах pm2 видно, какой коммит и UI-метки реально поднялись."""
    from content import messages as msg
    from platforms.build_info import get_build_info_text

    info = get_build_info_text()
    logger.info(
        "NeuroMule build %s",
        info.replace("\n", " | ").replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""),
    )
    logger.info("ui=%r table=%r studio=%r", msg.BTN_REPLY_NEUROTEXT, msg.BTN_TEXT_ROLE_TABLE, msg.BTN_STUDIO_MENU)


def build_bot() -> Bot:
    """Bot с опциональным прокси (``TELEGRAM_PROXY_URL``, HTTPS_PROXY, системный Windows)."""
    proxy = resolve_telegram_proxy_url(getattr(settings, "telegram_proxy_url", None))
    if proxy:
        session = AiohttpSession(proxy=proxy)
        return Bot(token=settings.tg_token, session=session)
    return Bot(token=settings.tg_token)


async def _wait_telegram_api(bot: Bot) -> None:
    """Проверка связи с Telegram API перед polling (с повторами при сетевых сбоях)."""
    last_error: Exception | None = None
    for attempt in range(1, _TELEGRAM_CONNECT_RETRIES + 1):
        try:
            me = await bot.get_me()
            logger.info("Telegram API OK: @%s (id=%s)", me.username, me.id)
            return
        except TelegramNetworkError as exc:
            last_error = exc
            logger.warning(
                "Telegram API недоступен (попытка %s/%s): %s",
                attempt,
                _TELEGRAM_CONNECT_RETRIES,
                exc,
            )
            if attempt < _TELEGRAM_CONNECT_RETRIES:
                await asyncio.sleep(_TELEGRAM_CONNECT_RETRY_SEC)
    has_proxy = bool(resolve_telegram_proxy_url(getattr(settings, "telegram_proxy_url", None)))
    proxy_hint = (
        " Прокси уже задан, но Telegram API всё равно недоступен — проверьте адрес/порт прокси."
        if has_proxy
        else (
            " С этого ПК не открывается api.telegram.org:443 (часто блокировка провайдера). "
            "Включите VPN или добавьте в .env, например: TELEGRAM_PROXY_URL=http://127.0.0.1:7890 "
            "(порт вашего Clash/V2Ray; для socks5:// нужен pip install aiohttp-socks)."
        )
    )
    raise RuntimeError(
        "Не удалось подключиться к Telegram API — бот не запущен."
        f"{proxy_hint}"
    ) from last_error


def build_fsm_storage():
    """Redis FSM для горизонтального масштаба; иначе MemoryStorage (dev/single-node)."""
    from aiogram.fsm.storage.memory import MemoryStorage

    url = (getattr(settings, "redis_url", None) or "").strip()
    if not url:
        logger.warning(
            "FSM storage: MemoryStorage — задайте REDIS_URL для multi-worker / highload"
        )
        return MemoryStorage()
    try:
        from aiogram.fsm.storage.redis import RedisStorage

        storage = RedisStorage.from_url(url)
        logger.info("FSM storage: Redis (horizontal-scale ready)")
        return storage
    except Exception:
        logger.exception(
            "FSM Redis unavailable (%s) — fallback MemoryStorage",
            url.split("@")[-1] if "@" in url else url,
        )
        return MemoryStorage()


def build_dispatcher() -> tuple[Bot, Dispatcher]:
    bot = build_bot()
    # generation_fsm и все FSMContext-хэндлеры берут storage отсюда.
    dp = Dispatcher(storage=build_fsm_storage())
    channel_sub = ChannelSubscription(bot)
    deps.bind(bot, channel_sub)

    # 1) Throttling — самым ВНЕШНИМ слоем: дешевле всего отбить дабл-клик
    # ещё до того, как aiogram доберётся до тяжёлых guard'ов вроде канала.
    throttling = ThrottlingMiddleware()
    dp.message.outer_middleware(throttling)
    dp.callback_query.outer_middleware(throttling)

    # 2) TOS-gate — жёсткая защита от любых апдейтов до accept_legal_tos.
    # ВАЖНО: ставим её ДО ChannelGate и реферальной обработки, чтобы
    # не списались /не начислились ресурсы у юзера без принятия оферты.
    tos_gate = TosGateMiddleware()
    dp.message.outer_middleware(tos_gate)
    dp.callback_query.outer_middleware(tos_gate)
    dp.inline_query.outer_middleware(tos_gate)

    daily_reset = DailyResetMiddleware()
    dp.message.outer_middleware(daily_reset)
    dp.callback_query.outer_middleware(daily_reset)
    dp.pre_checkout_query.outer_middleware(daily_reset)

    terms_gate = TermsGateMiddleware()
    dp.message.outer_middleware(terms_gate)
    dp.callback_query.outer_middleware(terms_gate)
    dp.pre_checkout_query.outer_middleware(terms_gate)

    channel_gate = ChannelGateMiddleware(channel_sub)
    dp.message.outer_middleware(channel_gate)
    dp.callback_query.outer_middleware(channel_gate)

    inbound = InboundUpdateMiddleware()
    dp.message.middleware(inbound)
    dp.callback_query.middleware(inbound)

    from platforms.telegram_middleware import ReplyNavDispatchMiddleware

    reply_nav = ReplyNavDispatchMiddleware()
    dp.message.middleware(reply_nav)

    from platforms.media_group_middleware import MediaGroupMiddleware

    dp.message.middleware(MediaGroupMiddleware())

    from platforms.summarizer_telegram import summarizer_router

    # До generation_fsm: перехват ввода в режиме «📄 Саммари» (ИИ-Ассистент).
    dp.include_router(summarizer_router)
    register_all(dp)
    _register_telegram_error_handler(dp)
    return bot, dp


def _register_telegram_error_handler(dp: Dispatcher) -> None:
    """Любое необработанное исключение в handler → лог + короткий ответ юзеру."""

    @dp.errors()
    async def _on_handler_error(event) -> bool:
        from aiogram.types import CallbackQuery, ErrorEvent

        if not isinstance(event, ErrorEvent):
            return False

        exc = event.exception
        update = event.update
        from aiogram.exceptions import TelegramConflictError

        if isinstance(exc, TelegramConflictError):
            logger.critical(
                "Telegram Conflict: второй процесс с тем же TG_TOKEN — "
                "остановите дубликат (pm2 delete neuromule; bash scripts/vdsina-update.sh)",
                exc_info=exc,
            )
        logger.error(
            "telegram handler error update_id=%s: %s",
            getattr(update, "update_id", None),
            exc,
            exc_info=exc,
        )

        notice = "⚠️ Что-то пошло не так. Попробуйте ещё раз или /start."
        try:
            if update.message is not None:
                await update.message.answer(notice)
            elif update.callback_query is not None:
                cb: CallbackQuery = update.callback_query
                await cb.answer(notice, show_alert=True)
                if cb.message is not None:
                    await cb.message.answer(notice)
        except Exception:
            logger.debug("error handler: failed to notify user", exc_info=True)
        return True


async def run_telegram() -> None:
    setup_logging(settings)
    if not settings.tg_token:
        raise RuntimeError("Задайте TG_TOKEN в .env")
    from services.billing.chat_pipeline import _collect_openrouter_keys

    if not _collect_openrouter_keys(settings):
        raise RuntimeError(
            "Задайте OPENROUTER_API_KEY или OPENROUTER_API_KEYS в .env"
        )

    try:
        from google import genai as _genai_probe  # noqa: F401
    except ImportError:
        logger.error(
            "Пакет google-genai НЕ установлен: «Совет дня»/HD пойдут через "
            "OpenRouter-fallback. На VDS: pip install 'google-genai>=1.0' "
            "&& pm2 restart neuromule-tg"
        )

    log_openrouter_proxy_configuration(settings)
    probe_openrouter_proxy(settings)
    await init_openrouter_http_client(settings)
    await _wait_openrouter_api(settings)

    # Отключаем авто-GC ДО старта polling. Дальше GC живёт исключительно
    # под controlled_gc_loop — никаких спонтанных 100-500 мс пауз на
    # горячем callback'е.
    setup_optimized_gc()

    await init_db(settings.promo_seeds)
    await start_dialog_write_worker()

    import asyncio as _asyncio
    # Фоновый GC кэша шеринга (24ч tick, TTL 48ч). Защищает RAM от
    # бесконечного роста при долгой работе процесса.
    _asyncio.create_task(clear_expired_cache_loop())
    # HintSession L2 GC: TTL 7 дней, tick раз в сутки (после init_db).
    _asyncio.create_task(clear_expired_hint_sessions_loop())
    # Контролируемый CPython GC: gen0→sleep(0)→gen1→sleep(0)→gen2 раз в 10 мин.
    # gc.collect идёт через run_in_executor → event loop не блокируется.
    _asyncio.create_task(controlled_gc_loop())
    # Живой каталог OpenRouter :free для FREE-каскада (refresh раз в час).
    from services.free_models_catalog import free_models_refresh_loop

    _asyncio.create_task(free_models_refresh_loop())
    # Пул «Совета дня»: Gemini-only refill в 00:05 МСК + дозаполнение при старте.
    from services.daily_advice_pool import daily_advice_pool_refill_loop

    _asyncio.create_task(daily_advice_pool_refill_loop())
    # Ежедневный отчёт админу о балансе OpenRouter в 09:00 МСК.
    from services.api_balance_monitor import api_balance_monitor_loop

    _asyncio.create_task(api_balance_monitor_loop())
    # Очередь AI-обложек блогера (OpenRouter Images) — один воркер, rate-limit 2с.
    from services.blogger_cover import start_cover_queue_worker

    await start_cover_queue_worker()
    # Observability sidecar (PR-K). Поднимается ТОЛЬКО если METRICS_HTTP_PORT>0
    # в .env. Bind строго на 127.0.0.1 — наружу выставляется через reverse-proxy.
    _metrics_port = int(getattr(settings, "metrics_http_port", 0) or 0)
    if _metrics_port > 0:
        _asyncio.create_task(serve_metrics(port=_metrics_port))

    bot, dp = build_dispatcher()

    _log_build_identity()

    # PR-P Phase 1a · опциональный PG-пул. Без DSN ничего не делаем —
    # production остаётся на SQLite, side-effect на existing-флоу нулевой.
    pg_pool = await _maybe_start_pg_pool(dp)

    await _wait_telegram_api(bot)
    await _setup_studio_menu_button(bot)
    print(f"{settings.bot_name} telegram: polling started.")
    try:
        await dp.start_polling(bot)
    finally:
        try:
            await close_openrouter_http_client()
        except Exception:
            logger.exception("openrouter http client close failed")
        if pg_pool is not None:
            try:
                await pg_pool.close()
                logger.info("postgres pool closed")
            except Exception:
                logger.exception("postgres pool close failed")
        try:
            from services.db_reports import close_financial_reports_db

            await close_financial_reports_db()
        except Exception:
            logger.exception("financial_reports engine close failed")


async def _maybe_start_pg_pool(dp: Dispatcher):
    """Поднять PG-пул, если задан ``POSTGRES_DSN``; иначе вернуть ``None``.

    Возвращённый pool регистрируется в ``dp.workflow_data["pg_pool"]``
    — оттуда aiogram автоматически инжектит его в любой handler с
    параметром ``pg_pool: Pool`` (см. ``payment_demo.py``).
    """

    dsn = (getattr(settings, "postgres_dsn", "") or "").strip()
    if not dsn:
        return None
    if init_postgres_pool is None:
        logger.error(
            "POSTGRES_DSN задан, но asyncpg не установлен — "
            "выполните `pip install asyncpg>=0.30`. Продолжаю на SQLite."
        )
        return None
    try:
        pool = await init_postgres_pool(dsn)
    except Exception:
        # Падение init pool НЕ должно валить весь бот: SQLite-флоу
        # остаётся source-of-truth до конца phase-2 миграции.
        logger.exception(
            "postgres pool init failed — продолжаю без PG (SQLite fallback)"
        )
        return None
    dp.workflow_data["pg_pool"] = pool
    logger.info("postgres pool attached to dispatcher (Phase 1a)")

    try:
        from services.db_reports import init_financial_reports_db

        await init_financial_reports_db(dsn)
    except Exception:
        logger.exception("financial_reports SQLAlchemy init failed — история отчётов недоступна")

    return pool
