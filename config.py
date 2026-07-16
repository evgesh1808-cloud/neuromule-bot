"""Чтение настроек из переменных окружения и файла ``.env`` (pydantic-settings)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

from pydantic import AliasChoices, BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_ENV_FILE = Path(__file__).resolve().with_name(".env")

# OpenRouter: FREE-тариф на ``:free`` / openrouter/free; платные — Gemini 2.5 Flash.
_DEFAULT_GEMINI_FLASH = "google/gemini-2.5-flash"
_DEFAULT_GEMINI_FLASH_LITE = "google/gemini-2.5-flash-lite"
# Роутер сам выбирает доступную :free-модель (ID часто ротируются у провайдеров).
_DEFAULT_FREE_CHAT_MODEL = "openrouter/free"
# Имя поля сохранено для обратной совместимости импортов/тестов.
_DEFAULT_GEMINI_FLASH_FREE = _DEFAULT_FREE_CHAT_MODEL
_DEFAULT_FREE_MODELS: list[str] = [
    "meta-llama/llama-3.2-3b-instruct:free",
    "google/gemma-4-31b-it:free",
]

_DEFAULT_SMART_MODELS: list[str] = [
    _DEFAULT_GEMINI_FLASH,
]


def _coerce_int(default: int) -> BeforeValidator:
    def _parse(v: Any) -> int:
        if v is None:
            return default
        if isinstance(v, bool):
            return default
        if isinstance(v, int):
            return v
        s = str(v).strip()
        if not s:
            return default
        try:
            return int(s)
        except ValueError:
            return default

    return BeforeValidator(_parse)


def _coerce_float(default: float) -> BeforeValidator:
    def _parse(v: Any) -> float:
        if v is None:
            return default
        if isinstance(v, float | int) and not isinstance(v, bool):
            return float(v)
        s = str(v).strip()
        if not s:
            return default
        try:
            return float(s)
        except ValueError:
            return default

    return BeforeValidator(_parse)


def _coerce_bool(default: bool) -> BeforeValidator:
    def _parse(v: Any) -> bool:
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if not s:
            return default
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
        return default

    return BeforeValidator(_parse)


def _coerce_str_list(default: list[str]) -> BeforeValidator:
    def _parse(v: Any) -> list[str]:
        if v is None:
            return list(default)
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        s = str(v).strip()
        if not s:
            return list(default)
        return [item.strip() for item in s.split(",") if item.strip()]

    return BeforeValidator(_parse)


def _coerce_int_list(default: list[int]) -> BeforeValidator:
    def _parse(v: Any) -> list[int]:
        if v is None:
            return list(default)
        if isinstance(v, list):
            out: list[int] = []
            for item in v:
                try:
                    out.append(int(str(item).strip()))
                except Exception:
                    continue
            return out
        s = str(v).strip()
        if not s:
            return list(default)
        out: list[int] = []
        for chunk in s.split(","):
            t = chunk.strip()
            if not t:
                continue
            try:
                out.append(int(t))
            except ValueError:
                continue
        return out or list(default)

    return BeforeValidator(_parse)


def _nonempty_str(default: str) -> BeforeValidator:
    """Как прежний ``_get_str``: пустая строка из env → значение по умолчанию."""

    def _parse(v: Any) -> str:
        if v is None:
            return default
        if not isinstance(v, str):
            return str(v)
        stripped = v.strip()
        return stripped if stripped else default

    return BeforeValidator(_parse)


def _strip_deprecated_free_suffix(model_id: str) -> str:
    """Нормализация model id (``:free`` сохраняем — нужен для FREE-тарифа)."""
    return (model_id or "").strip()


def _dedupe_model_ids(model_ids: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for mid in model_ids:
        norm = _strip_deprecated_free_suffix(mid)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def _openrouter_model_id(default: str) -> BeforeValidator:
    """Парсит model ID из env (включая суффикс ``:free``)."""

    def _parse(v: Any) -> str:
        if v is None:
            raw = default
        elif not isinstance(v, str):
            raw = str(v).strip()
        else:
            raw = v.strip() or default
        return _strip_deprecated_free_suffix(raw)

    return BeforeValidator(_parse)


def _openrouter_model_list(default: list[str]) -> BeforeValidator:
    """Парсит список model ID и снимает ``:free`` у каждого элемента."""

    def _parse(v: Any) -> list[str]:
        if v is None:
            items = list(default)
        elif isinstance(v, list):
            items = [str(x).strip() for x in v if str(x).strip()]
        else:
            s = str(v).strip()
            items = [item.strip() for item in s.split(",") if item.strip()] if s else list(default)
        normalized = _dedupe_model_ids(items or list(default))
        if normalized != items:
            logger.warning(
                "OpenRouter model list normalized (deprecated :free suffix): %r -> %r",
                items,
                normalized,
            )
        return normalized

    return BeforeValidator(_parse)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        populate_by_name=True,
        enable_decoding=False,
    )

    tg_token: str = Field(default="", validation_alias="TG_TOKEN")
    # Опциональный HTTP/SOCKS5-прокси для aiogram (если api.telegram.org недоступен напрямую).
    telegram_proxy_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_PROXY_URL", "telegram_proxy_url"),
    )
    # Опциональный HTTP/SOCKS5-прокси для OpenRouter (если Cloudflare блокирует IP хостинга).
    ai_proxy: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AI_PROXY", "ai_proxy"),
    )
    payment_token: str = Field(
        default="",
        validation_alias=AliasChoices("PAYMENT_TOKEN", "UKASSA_PROVIDER_TOKEN"),
    )
    yookassa_shop_id: str = Field(
        default="",
        validation_alias=AliasChoices("YOOKASSA_SHOP_ID", "yookassa_shop_id"),
    )
    yookassa_secret_key: str = Field(
        default="",
        validation_alias=AliasChoices("YOOKASSA_SECRET_KEY", "yookassa_secret_key"),
    )
    yookassa_return_url: Annotated[
        str, _nonempty_str("https://t.me/NeuroMule_bot")
    ] = "https://t.me/NeuroMule_bot"
    shop_payment_title: Annotated[str, _nonempty_str("NeuroMule")] = "NeuroMule"
    openrouter_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_chat_url: str = "https://openrouter.ai/api/v1/chat/completions"
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    replicate_api_token: str = Field(default="", alias="REPLICATE_API_TOKEN")
    replicate_video_model: Annotated[str, _nonempty_str("luma/ray-flash")] = "luma/ray-flash"
    replicate_animate_model: Annotated[str, _nonempty_str("luma/ray-flash")] = "luma/ray-flash"
    replicate_blogger_face_swap_model: Annotated[str, _nonempty_str("codeplugtech/face-swap")] = (
        "codeplugtech/face-swap"
    )
    replicate_poll_interval_sec: Annotated[float, _coerce_float(3.0)] = 3.0
    replicate_poll_timeout_sec: Annotated[float, _coerce_float(600.0)] = 600.0
    suno_api_token: str = Field(default="", alias="SUNO_API_TOKEN")
    suno_api_url: Annotated[str, _nonempty_str("https://suno.ai")] = "https://suno.ai"
    suno_make_instrumental: bool = False
    suno_wait_audio: bool = True
    suno_request_timeout_sec: Annotated[float, _coerce_float(300.0)] = 300.0
    bot_name: str = "NeuroMule"
    channel_id: str = "@mulendeeva_ai"
    channel_url: Annotated[str, _nonempty_str("https://t.me/mulendeeva_ai")] = "https://t.me/mulendeeva_ai"
    telegram_bot_username: Annotated[str, _nonempty_str("NeuroMule_bot")] = "NeuroMule_bot"
    support_bot_username: Annotated[str, _nonempty_str("MuleHelp_bot")] = "MuleHelp_bot"
    admin_username: Annotated[str, _nonempty_str("")] = ""
    admin_ids: Annotated[list[int], _coerce_int_list([])] = Field(default_factory=list)
    # Владелец секретной команды /admin_stats (если 0 — первый id из ADMIN_IDS).
    admin_telegram_id: Annotated[int, _coerce_int(0)] = Field(
        default=0,
        validation_alias=AliasChoices("ADMIN_TELEGRAM_ID", "admin_telegram_id"),
    )
    # Курс USD→RUB для финансового пульса (/admin_stats).
    admin_stats_usd_rub_rate: Annotated[float, _coerce_float(95.0)] = Field(
        default=95.0,
        validation_alias=AliasChoices("ADMIN_STATS_USD_RUB_RATE", "admin_stats_usd_rub_rate"),
    )
    # Gemini Flash через платный шлюз OpenRouter (иначе себестоимость Gemini = $0).
    openrouter_gemini_billable: Annotated[bool, _coerce_bool(False)] = Field(
        default=False,
        validation_alias=AliasChoices("OPENROUTER_GEMINI_BILLABLE", "openrouter_gemini_billable"),
    )
    god_mode_enabled: Annotated[bool, _coerce_bool(False)] = Field(
        default=False,
        validation_alias=AliasChoices("GOD_MODE_ENABLED", "god_mode_enabled"),
    )
    admin_chat_id: Annotated[int, _coerce_int(0)] = Field(
        default=0,
        validation_alias=AliasChoices("ADMIN_CHAT_ID", "admin_chat_id"),
    )
    vk_token: str = ""
    discord_token: str = Field(default="", validation_alias="DISCORD_TOKEN")
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    summarizer_model: Annotated[str, _nonempty_str("gpt-4o-mini")] = Field(
        default="gpt-4o-mini",
        validation_alias=AliasChoices("SUMMARIZER_MODEL", "summarizer_model"),
    )
    summarizer_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("SUMMARIZER_API_KEY", "summarizer_api_key"),
    )
    summarizer_api_host: Annotated[str, _nonempty_str("0.0.0.0")] = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("SUMMARIZER_API_HOST", "summarizer_api_host"),
    )
    summarizer_api_port: Annotated[int, _coerce_int(8010)] = Field(
        default=8010,
        validation_alias=AliasChoices("SUMMARIZER_API_PORT", "summarizer_api_port"),
    )
    summarizer_api_docs: Annotated[bool, _coerce_bool(False)] = Field(
        default=False,
        validation_alias=AliasChoices("SUMMARIZER_API_DOCS", "summarizer_api_docs"),
    )
    max_token: str = ""

    # ── Reviews & Gallery cross-posting (NeuroMule 🐎⚡️ • Виральный конвейер) ──
    # Чат-админка модерации отзывов: пересылка от пользователя на оценку модератору.
    reviews_admin_chat_id: Annotated[int, _coerce_int(0)] = Field(
        default=0,
        validation_alias=AliasChoices("REVIEWS_ADMIN_CHAT_ID", "reviews_admin_chat_id"),
    )
    # Публичный канал «Галерея шедевров» для одобренных результатов и отзывов.
    gallery_channel_id: str = ""
    # VK группа для кросс-постинга (album_id Photo/Video и group_id паблика).
    vk_group_id: Annotated[int, _coerce_int(0)] = Field(
        default=0,
        validation_alias=AliasChoices("VK_GROUP_ID", "vk_group_id"),
    )
    vk_group_token: str = ""
    vk_photo_album_id: Annotated[int, _coerce_int(0)] = Field(
        default=0,
        validation_alias=AliasChoices("VK_PHOTO_ALBUM_ID", "vk_photo_album_id"),
    )
    vk_video_album_id: Annotated[int, _coerce_int(0)] = Field(
        default=0,
        validation_alias=AliasChoices("VK_VIDEO_ALBUM_ID", "vk_video_album_id"),
    )
    vk_share_short_url: Annotated[
        str, _nonempty_str("https://vk.cc/neuromule_bot")
    ] = "https://vk.cc/neuromule_bot"
    # MAX App (видео-поток коротких роликов): bearer-токен и endpoint.
    max_api_token: str = ""
    max_api_url: Annotated[
        str, _nonempty_str("https://maxapp.ru/api/v1/feed/upload")
    ] = "https://maxapp.ru/api/v1/feed/upload"
    # Бонус Энергии за оставленный отзыв.
    review_energy_bonus: Annotated[int, _coerce_int(5)] = 5

    # ── Премодерация Галереи WebApp / TG-канала ────────────────────────
    # Отдельный чат-админка для PRE-публикационной модерации (NSFW и
    # вообще «спорный» контент). Если пуст — система деградирует в
    # авто-публикацию (как раньше) и пишет WARNING в лог.
    gallery_moderation_chat_id: Annotated[int, _coerce_int(0)] = Field(
        default=0,
        validation_alias=AliasChoices(
            "GALLERY_MODERATION_CHAT_ID", "gallery_moderation_chat_id"
        ),
    )

    # PR-K: HTTP-эндпоинт метрик. 0 = выключен (по умолчанию). При значении
    # > 0 в run_telegram() поднимается лёгкий FastAPI-sidecar, который
    # отвечает по http://127.0.0.1:{port}/metrics/json текущим snapshot'ом
    # из services.metrics. Bind строго на loopback — наружу выставляется
    # только через reverse-proxy (nginx/traefik) с авторизацией.
    metrics_http_port: Annotated[int, _coerce_int(0)] = Field(
        default=0,
        validation_alias=AliasChoices("METRICS_HTTP_PORT", "metrics_http_port"),
    )

    # Пул воркеров очереди AI-обложек блогера (общая asyncio.Queue).
    blogger_cover_workers_count: Annotated[int, _coerce_int(5)] = Field(
        default=5,
        validation_alias=AliasChoices(
            "BLOGGER_COVER_WORKERS_COUNT", "blogger_cover_workers_count"
        ),
    )

    # PR-P: PostgreSQL pool (DRAFT). Пустой DSN = legacy SQLite-флоу
    # (текущая прод-конфигурация). На фазе 1 миграции выставляется DSN
    # тестового PG-инстанса, на фазе 3 — production PG.
    # Формат: postgresql://user:pass@host:5432/dbname (можно с ?sslmode=require).
    postgres_dsn: str = Field(
        default="",
        validation_alias=AliasChoices("POSTGRES_DSN", "postgres_dsn"),
    )
    postgres_pool_min_size: Annotated[int, _coerce_int(2)] = Field(
        default=2,
        validation_alias=AliasChoices(
            "POSTGRES_POOL_MIN_SIZE", "postgres_pool_min_size"
        ),
    )
    postgres_pool_max_size: Annotated[int, _coerce_int(10)] = Field(
        default=10,
        validation_alias=AliasChoices(
            "POSTGRES_POOL_MAX_SIZE", "postgres_pool_max_size"
        ),
    )
    # Жёсткий таймаут на любой запрос. См. .cursorrules §1.9 / PR-P.
    postgres_command_timeout_sec: Annotated[float, _coerce_float(5.0)] = Field(
        default=5.0,
        validation_alias=AliasChoices(
            "POSTGRES_COMMAND_TIMEOUT_SEC", "postgres_command_timeout_sec"
        ),
    )
    gen_wait_note: Annotated[
        str,
        _nonempty_str("Подождите 1–3 минуты — генерация занимает время."),
    ] = "Подождите 1–3 минуты — генерация занимает время."
    promo_seeds: Annotated[str, _nonempty_str("")] = ""
    free_daily_photo_limit: Annotated[int, _coerce_int(3)] = 3
    free_daily_chat_limit: Annotated[int, _coerce_int(30)] = 30
    energy_low_threshold: Annotated[int, _coerce_int(50)] = 50
    cost_animate_video_suggest: Annotated[int, _coerce_int(20)] = 20

    cost_text_pro: Annotated[int, _coerce_int(10)] = 10
    cost_image_pro: Annotated[int, _coerce_int(3)] = 3
    free_imagen_overlimit_cost: Annotated[int, _coerce_int(2)] = 2
    paid_imagen_energy_cost: Annotated[int, _coerce_int(10)] = 10
    paid_imagen_crystal_cost: Annotated[int, _coerce_int(2)] = 2
    paid_flux_energy_cost: Annotated[int, _coerce_int(30)] = 30
    paid_flux_crystal_cost: Annotated[int, _coerce_int(3)] = 3
    paid_banana2_energy_cost: Annotated[int, _coerce_int(15)] = 15
    paid_banana2_crystal_cost: Annotated[int, _coerce_int(2)] = 2
    paid_banana_pro_energy_cost: Annotated[int, _coerce_int(35)] = 35
    paid_banana_pro_crystal_cost: Annotated[int, _coerce_int(3)] = 3
    cost_music: Annotated[int, _coerce_int(15)] = 15
    cost_video: Annotated[int, _coerce_int(20)] = 20
    cost_animate: Annotated[int, _coerce_int(20)] = 20
    cost_hd: Annotated[int, _coerce_int(70)] = 70
    cost_match: Annotated[int, _coerce_int(50)] = 50
    cost_upscale: Annotated[int, _coerce_int(1)] = 1
    referral_bonus_energy: Annotated[int, _coerce_int(5)] = 5
    referral_channel_crystals: Annotated[int, _coerce_int(2)] = 2

    # --- Чат (⚡ / 💎) ---
    cost_chat_standard_energy: Annotated[int, _coerce_int(1)] = 1
    cost_chat_standard_crystals: Annotated[int, _coerce_int(1)] = 1
    cost_chat_expert_energy: Annotated[int, _coerce_int(5)] = 5
    cost_chat_expert_crystals: Annotated[int, _coerce_int(3)] = 3
    daily_free_energy: Annotated[int, _coerce_int(30)] = 30

    # --- Видео-сценарии (💎) ---
    cost_video_pro_5sec: Annotated[int, _coerce_int(35)] = 35
    cost_video_extend: Annotated[int, _coerce_int(30)] = 30
    cost_video_long: Annotated[int, _coerce_int(100)] = 100
    cost_video_tier_50: Annotated[int, _coerce_int(50)] = 50
    cost_video_tier_70: Annotated[int, _coerce_int(70)] = 70
    cost_video_tier_80: Annotated[int, _coerce_int(80)] = 80
    cost_video_tier_100: Annotated[int, _coerce_int(100)] = 100
    cost_video_custom_text: Annotated[int, _coerce_int(40)] = 40
    cost_video_custom_photo: Annotated[int, _coerce_int(50)] = 50
    cost_video_custom_video: Annotated[int, _coerce_int(80)] = 80

    # --- Фото (платные модели) ---
    cost_image_dalle_crystals: Annotated[int, _coerce_int(5)] = 5

    service_offer_url: str = (
        "https://telegra.ph/Publichnaya-oferta-servisa-NeuroMule-05-20"
    )
    privacy_policy_url: str = (
        "https://telegra.ph/Politika-konfidencialnosti-servisa-NeuroMule-05-20"
    )
    subscription_terms_url: str = (
        "https://telegra.ph/Usloviya-regulyarnyh-platezhej-i-podpiski-NeuroMule-05-20"
    )
    support_instruction_url: str = (
        "https://telegra.ph/NeuroMule---Rukovodstvo-polzovatelya-05-20"
    )

    # WebApp Mini-App (Telegram / VK / MAX) — единый хостинг фронтенда магазина
    # тарифов и витрины «Галерея NeuroMule 2026».
    #
    # Гибкая конфигурация:
    #   * ``is_webapp_enabled=False`` (по умолчанию) → бот живёт в текстовом
    #     режиме: все «WebApp-точки входа» деградируют в обычные callback'и.
    #     Это безопасный rollout для prod без готового фронта.
    #   * ``is_webapp_enabled=True`` И ``webapp_shop_url`` задан →
    #     активируются WebApp-кнопки: одна большая «🚀 ОТКРЫТЬ ИИ-ПАНЕЛЬ»
    #     вместо create-menu и WebApp-кнопка «🚀 Пополнить баланс / Тарифы»
    #     в личном кабинете. Если ``is_webapp_enabled=True``, но URL пуст —
    #     бот не падает, а откатывается в текстовый режим (см. тесты).
    is_webapp_enabled: bool = False
    webapp_studio_url: str | None = None
    webapp_shop_url: str | None = None
    webapp_gallery_url: str | None = None
    # Базовый URL фронта таблиц (GitHub Pages). Плейсхолдер {report_id} или query report_id=.
    webapp_table_reports_url: str = (
        "https://your-user.github.io/neuromule-table/?report_id={report_id}"
    )
    # Публичный URL FastAPI Mini App backend (без trailing slash).
    # Пример: https://bot.example.com:8000 или http://127.0.0.1:8000
    mini_app_api_base_url: str = ""
    # CORS для Mini App API (``api/mini_app.py``). Список origin через запятую;
    # ``*`` не используется при ``allow_credentials=True`` — см. ``api/mini_app.py``.
    mini_app_cors_origins: str = ""
    # Максимальный возраст Telegram WebApp initData (секунды).
    mini_app_init_data_max_age_sec: Annotated[int, _coerce_int(86_400)] = 86_400

    free_models: Annotated[list[str], _openrouter_model_list(_DEFAULT_FREE_MODELS)] = Field(
        default_factory=lambda: list(_DEFAULT_FREE_MODELS)
    )
    smart_models: Annotated[list[str], _openrouter_model_list(_DEFAULT_SMART_MODELS)] = Field(
        default_factory=lambda: list(_DEFAULT_SMART_MODELS)
    )
    free_text_model: Annotated[
        str,
        _openrouter_model_id(_DEFAULT_FREE_CHAT_MODEL),
    ] = _DEFAULT_FREE_CHAT_MODEL
    paid_text_model: Annotated[
        str,
        _openrouter_model_id(_DEFAULT_GEMINI_FLASH),
    ] = _DEFAULT_GEMINI_FLASH
    free_image_model: Annotated[str, _nonempty_str("imagen4")] = "imagen4"
    free_daily_text_limit: Annotated[int, _coerce_int(30)] = 30

    # --- Магазин: пакеты (дефолты = утверждённая сетка 2026) ---
    mini_energy: Annotated[int, _coerce_int(500)] = 500
    mini_crystals: Annotated[int, _coerce_int(10)] = 10
    mini_rub_kopecks: Annotated[int, _coerce_int(34900)] = 34900
    mini_stars: Annotated[int, _coerce_int(250)] = 250
    mini_days: Annotated[int, _coerce_int(30)] = 30

    smart_energy: Annotated[int, _coerce_int(1500)] = 1500
    smart_crystals: Annotated[int, _coerce_int(35)] = 35
    smart_rub_kopecks: Annotated[int, _coerce_int(79000)] = 79000
    smart_stars: Annotated[int, _coerce_int(570)] = 570
    smart_days: Annotated[int, _coerce_int(30)] = 30

    ultra_3d_energy: Annotated[int, _coerce_int(500)] = 500
    ultra_3d_crystals: Annotated[int, _coerce_int(10)] = 10
    ultra_3d_rub_kopecks: Annotated[int, _coerce_int(29000)] = 29000
    ultra_3d_stars: Annotated[int, _coerce_int(210)] = 210
    ultra_3d_days: Annotated[int, _coerce_int(3)] = 3

    ultra_1w_energy: Annotated[int, _coerce_int(1800)] = 1800
    ultra_1w_crystals: Annotated[int, _coerce_int(35)] = 35
    ultra_1w_rub_kopecks: Annotated[int, _coerce_int(69000)] = 69000
    ultra_1w_stars: Annotated[int, _coerce_int(500)] = 500
    ultra_1w_days: Annotated[int, _coerce_int(7)] = 7

    ultra_1m_energy: Annotated[int, _coerce_int(7000)] = 7000
    ultra_1m_crystals: Annotated[int, _coerce_int(120)] = 120
    ultra_1m_rub_kopecks: Annotated[int, _coerce_int(249000)] = 249000
    ultra_1m_stars: Annotated[int, _coerce_int(1800)] = 1800
    ultra_1m_days: Annotated[int, _coerce_int(30)] = 30

    # Legacy aliases (старые импорты)
    ultra_energy: Annotated[int, _coerce_int(7000)] = 7000
    ultra_crystals: Annotated[int, _coerce_int(120)] = 120
    ultra_rub_kopecks: Annotated[int, _coerce_int(249000)] = 249000
    ultra_stars: Annotated[int, _coerce_int(1800)] = 1800

    crystals_10_amount: Annotated[int, _coerce_int(10)] = 10
    crystals_10_rub_kopecks: Annotated[int, _coerce_int(24900)] = 24900
    crystals_10_stars: Annotated[int, _coerce_int(180)] = 180

    crystals_40_amount: Annotated[int, _coerce_int(40)] = 40
    crystals_40_rub_kopecks: Annotated[int, _coerce_int(69000)] = 69000
    crystals_40_stars: Annotated[int, _coerce_int(500)] = 500

    crystals_100_amount: Annotated[int, _coerce_int(100)] = 100
    crystals_100_rub_kopecks: Annotated[int, _coerce_int(149000)] = 149000
    crystals_100_stars: Annotated[int, _coerce_int(1080)] = 1080

    chat_history_limit: Annotated[int, _coerce_int(6)] = 6
    chat_max_message_chars: Annotated[int, _coerce_int(8000)] = 8000
    dialog_prune_keep: Annotated[int, _coerce_int(50)] = 50
    chat_rate_limit_per_minute: Annotated[int, _coerce_int(30)] = 30
    openrouter_timeout_sec: Annotated[float, _coerce_float(45.0)] = 45.0
    # Таймаут одного запроса FREE-каскада (короче — меньше «висит typing»).
    openrouter_free_timeout_sec: Annotated[float, _coerce_float(25.0)] = 25.0
    # WB CFO: OpenRouter только для HTML-обёртки (по умолчанию локальный отчёт — без задержек).
    wb_finance_openrouter_html: Annotated[bool, _coerce_bool(False)] = Field(
        default=False,
        validation_alias=AliasChoices("WB_FINANCE_OPENROUTER_HTML", "wb_finance_openrouter_html"),
    )

    # Логи: каталог относительно корня проекта (рядом с config.py / .env).
    log_dir: Annotated[str, _nonempty_str("logs")] = "logs"
    log_max_bytes: Annotated[int, _coerce_int(10 * 1024 * 1024)] = 10 * 1024 * 1024
    log_backup_count: Annotated[int, _coerce_int(5)] = 5
    log_console: bool = True

    # Rate limit: при непустом URL — Redis (окно по минутам), иначе SQLite-таблица в основной БД.
    redis_url: str = ""

    # Оценка «входных токенов» для чата (грубо: сумма len(content)//char_per_token_est).
    chat_char_per_token_est: Annotated[int, _coerce_int(3)] = 3
    chat_max_context_tokens_est: Annotated[int, _coerce_int(24_000)] = 24_000
    # Лимит ответа модели (выходные токены) — FREE / базовый чат; 500–800 — разумный дефолт.
    openrouter_max_output_tokens: Annotated[int, _coerce_int(640)] = 640
    # Потолок ответа для платных тарифов (развёрнутый Стандарт; API max_tokens).
    openrouter_premium_max_output_tokens: Annotated[int, _coerce_int(2800)] = 2800
    # Роль table_generator: компактный JSON вместо Markdown-таблицы.
    openrouter_table_max_output_tokens: Annotated[int, _coerce_int(1500)] = 1500

    # Стриминг в Telegram: минимальный интервал между edit_message_text (антифлуд API).
    telegram_chat_streaming: bool = True
    telegram_stream_edit_interval_sec: Annotated[float, _coerce_float(0.8)] = 0.8

    # Длинный ответ в Telegram: несколько сообщений по порогу (символов).
    chat_chunk_reply_threshold: Annotated[int, _coerce_int(3500)] = 3500
    chat_reply_chunk_size: Annotated[int, _coerce_int(3900)] = 3900

    # Подсчёт токенов контекста: tiktoken (имя кодировки) или откат на эвристику по символам.
    chat_use_tiktoken: bool = True
    tiktoken_encoding: Annotated[str, _nonempty_str("cl100k_base")] = "cl100k_base"

    # Очередь записи assistant+prune в SQLite (один фоновый воркер); в тестах воркер не стартует — прямой коммит.
    dialog_write_worker_enabled: bool = True

    # --- WB API nightly worker (workers/wb_api_worker.py) ---
    wb_api_base_url: Annotated[
        str, _nonempty_str("https://statistics-api.wildberries.ru")
    ] = "https://statistics-api.wildberries.ru"
    wb_api_timeout_sec: Annotated[float, _coerce_float(30.0)] = 30.0
    wb_api_poll_interval_sec: Annotated[float, _coerce_float(60.0)] = 60.0
    wb_api_batch_hour: Annotated[int, _coerce_int(3)] = 3
    wb_api_morning_hour: Annotated[int, _coerce_int(9)] = 9
    wb_api_morning_minute: Annotated[int, _coerce_int(0)] = 0
    wb_api_run_batch_on_start: Annotated[bool, _coerce_bool(True)] = True

    # --- WB тарифы: ночной кэш «Робот для Робота» (services/wb_tariffs_cache.py) ---
    master_wb_api_token: str = ""
    wb_tariffs_api_base_url: Annotated[
        str, _nonempty_str("https://common-api.wildberries.ru")
    ] = "https://common-api.wildberries.ru"
    wb_tariffs_cache_path: str = ""

    # cfo-v12: личные Statistics API ключи пользователей отключены (только Excel + MASTER_WB).
    wb_user_statistics_api_enabled: Annotated[bool, _coerce_bool(False)] = False


settings = Settings()


# God Mode: Telegram ID супер-админов задаются в .env как ADMIN_IDS=123456789,987654321
# Включение обхода биллинга: GOD_MODE_ENABLED=1 (по умолчанию выключено на проде).
# Runtime: settings.admin_ids, settings.god_mode_enabled  |  billing_bypass(user_id)
ADMIN_IDS: list[int] = settings.admin_ids


# Короткие module-level алиасы юридических ссылок (Telegra.ph). Удобно
# импортировать в handler'ах без обращения к settings.* — это стабильные
# константы, идентичные содержимому соответствующих полей Settings.
URL_PUBLIC_OFFER: str = settings.service_offer_url
URL_PRIVACY_POLICY: str = settings.privacy_policy_url
URL_SUBSCRIPTION_TERMS: str = settings.subscription_terms_url
