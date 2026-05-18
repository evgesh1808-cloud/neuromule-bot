"""Чтение настроек из переменных окружения и файла ``.env`` (pydantic-settings)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from pydantic import AliasChoices, BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().with_name(".env")

_DEFAULT_FREE_MODELS: list[str] = [
    "google/gemini-2.0-flash-lite-preview-02-05:free",
    "google/gemini-2.0-pro-exp-02-05:free",
    "mistralai/mistral-7b-instruct:free",
    "openrouter/auto",
]

_DEFAULT_SMART_MODELS: list[str] = [
    "openrouter/auto",
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
    payment_token: str = Field(
        default="",
        validation_alias=AliasChoices("PAYMENT_TOKEN", "UKASSA_PROVIDER_TOKEN"),
    )
    shop_payment_title: Annotated[str, _nonempty_str("NeuroMule")] = "NeuroMule"
    openrouter_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_chat_url: str = "https://openrouter.ai/api/v1/chat/completions"
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    replicate_api_token: str = Field(default="", alias="REPLICATE_API_TOKEN")
    replicate_video_model: Annotated[str, _nonempty_str("luma/ray-flash")] = "luma/ray-flash"
    replicate_animate_model: Annotated[str, _nonempty_str("luma/ray-flash")] = "luma/ray-flash"
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
    vk_token: str = ""
    max_token: str = ""
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
    cost_image_pro: Annotated[int, _coerce_int(2)] = 2
    cost_music: Annotated[int, _coerce_int(5)] = 5
    cost_video: Annotated[int, _coerce_int(20)] = 20
    cost_animate: Annotated[int, _coerce_int(20)] = 20
    cost_hd: Annotated[int, _coerce_int(70)] = 70
    cost_match: Annotated[int, _coerce_int(50)] = 50
    cost_upscale: Annotated[int, _coerce_int(1)] = 1
    referral_bonus_energy: Annotated[int, _coerce_int(50)] = 50

    service_offer_url: str = (
        "https://telegra.ph/Polzovatelskoe-soglashenie-i-Oferta-04-30"
    )
    privacy_policy_url: str = (
        "https://telegra.ph/Politika-konfidencialnosti-Obrabotka-personalnyh-dannyh-04-30-2"
    )
    subscription_terms_url: str = (
        "https://telegra.ph/Soglashenie-o-rekurrentnyh-platezhah-Podpiska-04-30-2"
    )

    free_models: Annotated[list[str], _coerce_str_list(_DEFAULT_FREE_MODELS)] = Field(
        default_factory=lambda: list(_DEFAULT_FREE_MODELS)
    )
    smart_models: Annotated[list[str], _coerce_str_list(_DEFAULT_SMART_MODELS)] = Field(
        default_factory=lambda: list(_DEFAULT_SMART_MODELS)
    )
    free_text_model: Annotated[
        str,
        _nonempty_str("google/gemini-2.0-flash-lite:free"),
    ] = "google/gemini-2.0-flash-lite:free"
    free_image_model: Annotated[str, _nonempty_str("flux-schnell")] = "flux-schnell"
    free_daily_text_limit: Annotated[int, _coerce_int(30)] = 30

    mini_energy: Annotated[int, _coerce_int(500)] = 500
    mini_crystals: Annotated[int, _coerce_int(10)] = 10
    mini_rub_kopecks: Annotated[int, _coerce_int(29000)] = 29000
    mini_stars: Annotated[int, _coerce_int(210)] = 210

    smart_energy: Annotated[int, _coerce_int(1500)] = 1500
    smart_crystals: Annotated[int, _coerce_int(35)] = 35
    smart_rub_kopecks: Annotated[int, _coerce_int(69000)] = 69000
    smart_stars: Annotated[int, _coerce_int(490)] = 490

    ultra_energy: Annotated[int, _coerce_int(7000)] = 7000
    ultra_crystals: Annotated[int, _coerce_int(120)] = 120
    ultra_rub_kopecks: Annotated[int, _coerce_int(199000)] = 199000
    ultra_stars: Annotated[int, _coerce_int(1450)] = 1450

    crystals_10_amount: Annotated[int, _coerce_int(10)] = 10
    crystals_10_rub_kopecks: Annotated[int, _coerce_int(19900)] = 19900
    crystals_10_stars: Annotated[int, _coerce_int(145)] = 145

    crystals_40_amount: Annotated[int, _coerce_int(40)] = 40
    crystals_40_rub_kopecks: Annotated[int, _coerce_int(49000)] = 49000
    crystals_40_stars: Annotated[int, _coerce_int(355)] = 355

    crystals_100_amount: Annotated[int, _coerce_int(100)] = 100
    crystals_100_rub_kopecks: Annotated[int, _coerce_int(99000)] = 99000
    crystals_100_stars: Annotated[int, _coerce_int(720)] = 720

    chat_history_limit: Annotated[int, _coerce_int(10)] = 10
    chat_max_message_chars: Annotated[int, _coerce_int(8000)] = 8000
    dialog_prune_keep: Annotated[int, _coerce_int(50)] = 50
    chat_rate_limit_per_minute: Annotated[int, _coerce_int(30)] = 30
    openrouter_timeout_sec: Annotated[float, _coerce_float(45.0)] = 45.0

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
    # Лимит ответа модели (выходные токены) — защита бюджета OpenRouter; 500–800 — разумный дефолт.
    openrouter_max_output_tokens: Annotated[int, _coerce_int(640)] = 640

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


settings = Settings()
