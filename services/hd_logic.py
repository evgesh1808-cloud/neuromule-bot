"""HD Premium: Gemini report generation, SQLite helpers, and PDF export."""
from __future__ import annotations

import asyncio
import html as html_module
import json
import logging
import os
import random
import tempfile
import re
import textwrap
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from config import settings as _app_settings

try:
    from google import genai
except ImportError:  # pragma: no cover - surfaced at runtime in the handler.
    genai = None

try:
    import swisseph as swe
except ImportError:  # pragma: no cover - surfaced at runtime in the handler.
    swe = None

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:  # pragma: no cover - surfaced at runtime in the handler.
    Image = None  # type: ignore[misc, assignment]
    ImageDraw = None  # type: ignore[misc, assignment]
    ImageFilter = None  # type: ignore[misc, assignment]
    ImageFont = None  # type: ignore[misc, assignment]

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader, simpleSplit
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        Image as RLImage,
        PageBreak,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.platypus.doctemplate import NextPageTemplate
    from reportlab.platypus.flowables import Flowable
except ImportError:  # pragma: no cover - surfaced at runtime in the handler.
    colors = None
    TA_CENTER = None
    A4 = None
    ParagraphStyle = None
    mm = None
    simpleSplit = None
    pdfmetrics = None
    TTFont = None
    canvas = None
    BaseDocTemplate = None
    Frame = None
    RLImage = None
    PageBreak = None
    PageTemplate = None
    Paragraph = None
    Spacer = None
    Table = None
    TableStyle = None
    NextPageTemplate = None
    Flowable = None

try:
    from services.repository import DB_PATH as REPOSITORY_DB_PATH
except Exception:  # pragma: no cover
    REPOSITORY_DB_PATH = "/app/data/neuromule_base.db"


logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", REPOSITORY_DB_PATH)


def get_hd_report_cost() -> int:
    """Стоимость полного HD-разбора (env ``COST_HD`` → ``settings.cost_hd``)."""
    return _app_settings.cost_hd


def get_match_report_cost() -> int:
    """Стоимость отчёта совместимости (env ``COST_MATCH``)."""
    return _app_settings.cost_match


# Обратная совместимость импортов (значения из .env при загрузке модуля).
HD_REPORT_COST = get_hd_report_cost()
MATCH_REPORT_COST = get_match_report_cost()
PRICE_UPSCALE = _app_settings.cost_upscale

# Канал B (Gemini SDK): отдельные каскады для «Совета дня» (Flash) и премиум-разбора (Pro).
_GEMINI_DAILY_MODEL_CHAIN: tuple[str, ...] = (
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash-latest",
)
_GEMINI_PREMIUM_MODEL_CHAIN: tuple[str, ...] = (
    "gemini-2.5-pro",
    "gemini-2.0-pro-exp-02-15",
    "gemini-1.5-pro-latest",
)
# Обратная совместимость для daily_advice_pool (ночной cron).
_GEMINI_MODEL_CHAIN = _GEMINI_DAILY_MODEL_CHAIN
_HD_WATERMARK = "🧬 Создано в @neuromule_bot"
_HD_WATERMARK_PLAIN = "Создано в @neuromule_bot"
_HD_NEON_HEX = "#8B5CF6"
_ENERGY_SCALE_KEYS = ("capacity", "immunity", "scale")
_COMPAT_REPORT_KEYS = ("attraction", "conflicts", "growth")
# Таймаут одной Gemini-модели в «Совете дня» (сек); дальше — следующая / OpenRouter.
_GEMINI_DAILY_TIMEOUT_SEC = 20.0
_OPENROUTER_DAILY_TIMEOUT_SEC = 45.0
_OPENROUTER_DAILY_MAX_TOKENS = 900
_GEMINI_PREMIUM_TIMEOUT_SEC = 90.0
_OPENROUTER_PREMIUM_TIMEOUT_SEC = 120.0
_OPENROUTER_PREMIUM_UPGRADE_TIMEOUT_SEC = 50.0
_HD_UPGRADE_LLM_TIMEOUT_SEC = 150.0
_HD_PREMIUM_MAX_OUTPUT_TOKENS = 8192
_PDF_FONT_NAME = "HDReportFont"
_PDF_COVER_BG = "#0D0E12"
_PDF_CONTENT_BG = "#FAFAFA"
_PDF_BODYGRAPH_WIDTH_PX = 430
_PDF_BODYGRAPH_MAX_BYTES = 300 * 1024
_PDF_CHAPTER_SPECS: tuple[tuple[str, str, str], ...] = (
    ("static_reference", "📚 Справочник карты (IHDS)", "hd_ch_static"),
    ("money", "💼 Раздел: Финансовый Аудит", "hd_ch_money"),
    ("love", "❤️ Раздел: Отношения и Партнёрство", "hd_ch_love"),
    ("energy", "⚡ Раздел: Энергетическая Архитектура", "hd_ch_energy"),
    ("plan", "📅 Раздел: План на 30 дней", "hd_ch_plan"),
)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PREMIUM_REPORT_KEYS = ("fast_facts", "money", "love", "energy", "plan")
_HD_REPORT_SCHEMA_VERSION = 3
_HD_REPORT_SCHEMA_VERSION_SYNTHESIS = 3
_LEGACY_HD_REPORT_PLACEHOLDER = "⚡ Экспресс-анализ доступен в интерактивном разборе."
_FAST_FACTS_MAX_LEN = 300
_PREMIUM_SUMMARY_TEMPERATURE = 0.25
_PREMIUM_SUMMARY_MAX_TOKENS = 2048
_MAX_SYNTHESIS_PAIRS_FULL = 9
_MAX_SYNTHESIS_PAIRS_UPGRADE = 2
_GENETIC_SYNTHESIS_DOMAINS: frozenset[str] = frozenset({"money", "love", "energy"})
_GENETIC_SYNTHESIS_TEMPERATURE = 0.1
_GENETIC_SYNTHESIS_MAX_TOKENS = 4096
_SYNTHESIS_EXPERIMENT_TIMEFRAMES: tuple[str, ...] = ("days_1-5", "days_6-15", "days_16-30")
_SYNTHESIS_STRING_KEYS: tuple[str, ...] = (
    "synthesis_anchor",
    "client_pain",
    "false_self_pattern",
    "body_signal",
)
_HD_MOTOR_CENTERS: frozenset[str] = frozenset({"Сакрал", "Эго", "Корень", "Солнечное сплетение"})
_HD_GATE_SEQUENCE = (
    25,
    17,
    21,
    51,
    42,
    3,
    27,
    24,
    2,
    23,
    8,
    20,
    16,
    35,
    45,
    12,
    15,
    52,
    39,
    53,
    62,
    56,
    31,
    33,
    7,
    4,
    29,
    59,
    40,
    64,
    47,
    6,
    46,
    18,
    48,
    57,
    32,
    50,
    28,
    44,
    1,
    43,
    14,
    34,
    9,
    5,
    26,
    11,
    10,
    58,
    38,
    54,
    61,
    60,
    41,
    19,
    13,
    49,
    30,
    55,
    37,
    63,
    22,
    36,
)
_WEEKDAY_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


DailyAdviceUserProfile = dict[str, str]

# Сигнал для Gemini: тип нужно вычислить по дате/времени/месту рождения (нет платного разбора).
_HD_TYPE_UNDETERMINED = "НЕ ОПРЕДЕЛЕН"
_DEFAULT_ADVICE_USER_ROLE = "по умолчанию"

_DAILY_FORECAST_PROMPT = (
    "Ты — харизматичный цифровой коуч NeuroMule 🐎⚡️, топ-эксперт по Дизайну Человека (Human Design). "
    'Сгенерируй короткий, вдохновляющий и строго персонализированный "Совет дня" от лица NeuroMule.\n\n'
    "ВХОДНЫЕ ДАННЫЕ:\n"
    "- Дата: {current_date} ({day_of_week})\n"
    "- Тип личности (из БД): {hd_type}\n"
    "- Роль / сфера: {user_role}\n"
    "- Рождение: {birth_date}, {birth_time}, город {birth_place}\n"
    "- Оффер дня (вставить в конец дословно после пустой строки): {current_cta_text}\n"
    "- Подписка на @mulendeeva_ai: Активна\n\n"
    "ЛОГИКА ТИПА HUMAN DESIGN:\n"
    "- Если в поле «Тип личности» передан конкретный тип (не «НЕ ОПРЕДЕЛЕН») — используй его "
    "в заголовке блока «ТВОЙ НАВИГАТОР» КАПСОЛОМ (например, ГЕНЕРАТОР).\n"
    "- Если передано «НЕ ОПРЕДЕЛЕН» — самостоятельно вычисли тип (Генератор, Манифестор, Проектор "
    "или Рефлектор) по {birth_date}, {birth_time}, {birth_place}, используя знания астрономических "
    "эфемерид Human Design. Выведи вычисленный тип КАПСОЛОМ в заголовок «ТВОЙ НАВИГАТОР» и строй "
    "совет строго на основе его Стратегии. Категорически ЗАПРЕЩЕНО писать пользователю "
    "«тип не определён» или «НЕ ОПРЕДЕЛЕН».\n"
    "КОНТЕКСТ: Swiss Ephemeris и бодиграф в коде НЕ считаются — чистая LLM-интерпретация.\n\n"
    "ЖЁСТКИЕ ПРАВИЛА TELEGRAM (СТРОГО):\n"
    "1. КАТЕГОРИЧЕСКИ БЕЗ HTML-тегов (<b>, <i>, <a> и т.д.).\n"
    "2. КАТЕГОРИЧЕСКИ БЕЗ Markdown: запрещены *, **, _, ` и любая разметка.\n"
    "3. Акценты — только ЭМОДЗИ и фрагменты ВЕРХНИМ РЕГИСТРОМ (КАПСОМ).\n"
    "4. Блоки разделяй ровно одной пустой строкой (\\n\\n).\n"
    '5. ЗАПРЕЩЕНО писать: "ИИ", "Искусственный интеллект", "бот", "нейросеть", "модель".\n'
    "6. Тон: тёплый, бытовой, поддерживающий; без эзотерического сленга "
    '("вибрации", "нейтрино", "обуславливание").\n'
    "7. Общий объём основного текста (до оффера) — до 6–7 предложений, лаконично.\n\n"
    "СТРОГО СЛЕДУЙ СТРУКТУРЕ (заголовки копируй один в один):\n\n"
    "🌌 ЗВЕЗДНЫЙ БАРОМЕТР NEUROMULE 🐎⚡️\n"
    "(Напиши общую планетарную погоду и космические вибрации на сегодня для всех людей, "
    "строго 1–2 предложения. Категорически ЗАПРЕЩЕНО упоминать город рождения {birth_place} "
    "в тексте, так как пользователь может жить в другой стране или городе.)\n\n"
    "🔮 ТВОЙ НАВИГАТОР (Сюда подставь рассчитанный или переданный ТИП ЛИЧНОСТИ КАПСОЛОМ)\n"
    "(Дай совет типу личности в его текущей роли {user_role}, строго 2 предложения, "
    "опираясь на механику Human Design)\n\n"
    "🎯 ПРОСТОЙ ШАГ В ПЛЮС\n"
    "• (ровно одно легкое бытовое действие на 2–5 минут)\n\n"
    "⚠️ КУДА НЕ СЛИВАТЬ СИЛЫ\n"
    "• (чего именно избегать сегодня, ловушка ума)\n\n"
    "[ровно одна пустая строка]\n"
    "{current_cta_text}"
)


def birth_context_lines_for_daily_advice(hd_line: str, advice_only_line: str) -> str | None:
    """Собирает контекст рождения для совета дня (платный разбор имеет приоритет)."""
    h = (hd_line or "").strip()
    a = (advice_only_line or "").strip()
    chosen = h or a or ""
    return chosen if chosen else None


def parse_birth_for_daily_advice(raw: str) -> dict[str, str]:
    """Дата, время, место и опциональная роль из одной строки/блока рождения."""
    text = (raw or "").strip()
    user_role = _DEFAULT_ADVICE_USER_ROLE
    hd_type_inline = ""
    body_lines: list[str] = []
    for line in text.splitlines():
        low = line.lower().strip()
        if low.startswith("роль:"):
            user_role = line.split(":", 1)[1].strip() or user_role
        elif low.startswith("тип:"):
            hd_type_inline = line.split(":", 1)[1].strip()
        else:
            body_lines.append(line)
    body = "\n".join(body_lines).strip() or text

    birth_date = "не указана"
    birth_time = "не указано"
    nums = _extract_birth_numbers(text)
    if nums:
        year, month, day, hour, minute = nums
        birth_date = f"{day:02d}.{month:02d}.{year}"
        if re.search(r"(\d{1,2})[:.](\d{2})", text):
            birth_time = f"{hour:02d}:{minute:02d}"

    place = re.sub(
        r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})(?:\D+(\d{1,2})[:.](\d{2}))?",
        " ",
        body,
        count=1,
    )
    place = re.sub(r"\s+", " ", place).strip(" ,.;")
    birth_place = place or "не указан"

    return {
        "birth_date": birth_date,
        "birth_time": birth_time,
        "birth_place": birth_place,
        "user_role": user_role,
        "hd_type_inline": hd_type_inline,
    }


def _resolve_hd_type_for_advice(hd_birth_data: str, db_hd_type: str) -> str:
    """
    Платный разбор (hd_birth_data + hd_type в БД) → готовый тип; иначе «НЕ ОПРЕДЕЛЕН» для Gemini.
    """
    if (hd_birth_data or "").strip() and (db_hd_type or "").strip():
        return db_hd_type.strip()
    return _HD_TYPE_UNDETERMINED


def daily_advice_user_profile_from_repo_user(user: object) -> DailyAdviceUserProfile | None:
    """
    Собирает профиль для совета дня из строки users (get_user / aiosqlite.Row).

    Ключи: hd_type, user_role, birth_date, birth_time, birth_place.
  """
    keys = user.keys() if hasattr(user, "keys") else []

    def _col(name: str) -> str:
        if name not in keys:
            return ""
        val = user[name]
        return str(val).strip() if val is not None else ""

    hd_bd = _col("hd_birth_data")
    adv_bd = _col("advice_birth_data")
    birth_notes = birth_context_lines_for_daily_advice(hd_bd, adv_bd)
    if not birth_notes:
        return None

    parsed = parse_birth_for_daily_advice(birth_notes)
    hd_type = _resolve_hd_type_for_advice(hd_bd, _col("hd_type"))

    user_role = _col("advice_user_role") or parsed.get("user_role") or _DEFAULT_ADVICE_USER_ROLE

    return {
        "hd_type": hd_type,
        "user_role": user_role,
        "birth_date": parsed["birth_date"],
        "birth_time": parsed["birth_time"],
        "birth_place": parsed["birth_place"],
        # Сырая строка для локального пересечения с эфемеридами дня (0 LLM).
        "birth_raw": birth_notes,
    }


_MSK_TZ = timezone(timedelta(hours=3))

_CTA_MONDAY_PHOTO: tuple[str, ...] = (
    "📷 Обнови аватар: ИИ проявит твою истинную ауру на фото — {photo} 💎",
    "📸 Твой сильный визуальный образ: ИИ создаст портрет под твою роль — {photo} 💎",
    "🖼️ Взгляни на себя со стороны: сгенерируй ИИ-аватар своего дизайна — {photo} 💎",
    "✨ Прояви свою силу через визуал: ИИ-фотосет под твою энергетику — {photo} 💎",
)
_CTA_TUESDAY_VIDEO: tuple[str, ...] = (
    "🎬 Увидь свой дизайн в движении: создай короткое ИИ-видео — {video} 💎",
    "🎥 Перенеси свои смыслы на экран: ИИ сгенерирует ролик под твой день — {video} 💎",
    "🎞️ Прояви внутреннюю силу в динамике: запусти ИИ-видеогенерацию — {video} 💎",
    "📹 Твой потенциал на кинопленке: создай завораживающее ИИ-видео — {video} 💎",
)
_CTA_WEDNESDAY_AUDIO: tuple[str, ...] = (
    "🎸 Сонастройся с космосом: ИИ создаст твой личный трек на сегодня — {audio} 💎",
    "🎵 Послушай ритм своей ауры: разблокируй персональный ИИ-звук дня — {audio} 💎",
    "🔊 Переведи механику дизайна в музыку: сгенерируй личную ИИ-мелодию — {audio} 💎",
    "🎧 Поймай свою волну: включи индивидуальный ИИ-трек под твой дизайн — {audio} 💎",
)
_CTA_THURSDAY_ANIMATE: tuple[str, ...] = (
    "✨ Оживи любимый снимок: ИИ вдохнет жизнь в застывший момент — {animate} 💎",
    "💫 Магия в один клик: преврати статичное фото в живую ИИ-картину — {animate} 💎",
    "🌌 Запусти движение: позволь нейросети оживить любую фотографию — {animate} 💎",
    "🔮 Вдохни динамику в кадр: оживление любого фото силами ИИ — {animate} 💎",
)
_CTA_WEEKEND_FULL_REPORT: tuple[str, ...] = (
    "🔮 Этот совет — лишь 1% твоей силы. Узнай про свои деньги и таланты в Полном Разборе",
    "💎 Твоя карта сокровищ скрыта глубже. Разблокируй Полный Разбор Хьюман Дизайн",
    "👑 Полноценный навигатор по твоей жизни: открой свой глубокий Полный Разбор",
)


def _cta_cost_from_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def get_dynamic_cta_for_today(now: datetime | None = None) -> str:
    """Короткий рандомный CTA для «Совета дня» по дню недели (МСК, цены из env)."""
    if now is None:
        moment = datetime.now(_MSK_TZ)
    elif now.tzinfo is None:
        moment = now.replace(tzinfo=_MSK_TZ)
    else:
        moment = now.astimezone(_MSK_TZ)

    photo = _cta_cost_from_env("PHOTO_COST", 20)
    video = _cta_cost_from_env("VIDEO_COST", 25)
    audio = _cta_cost_from_env("AUDIO_COST", 15)
    animate = _cta_cost_from_env("ANIMATE_COST", 20)

    weekday = moment.weekday()
    if weekday == 0:
        template = random.choice(_CTA_MONDAY_PHOTO)
        return template.format(photo=photo)
    if weekday == 1:
        template = random.choice(_CTA_TUESDAY_VIDEO)
        return template.format(video=video)
    if weekday == 2:
        template = random.choice(_CTA_WEDNESDAY_AUDIO)
        return template.format(audio=audio)
    if weekday == 3:
        template = random.choice(_CTA_THURSDAY_ANIMATE)
        return template.format(animate=animate)
    return random.choice(_CTA_WEEKEND_FULL_REPORT)


def build_daily_advice_prompt(
    user_profile: DailyAdviceUserProfile,
    *,
    current_cta_text: str,
    now: datetime | None = None,
) -> str:
    """Подставляет поля профиля и дату в ``_DAILY_FORECAST_PROMPT``."""
    moment = now or datetime.now()
    current_date_str = moment.strftime("%d.%m.%Y")
    return _DAILY_FORECAST_PROMPT.format(
        current_date=current_date_str,
        day_of_week=_WEEKDAY_RU[moment.weekday()],
        current_cta_text=(current_cta_text or "").strip(),
        hd_type=user_profile.get("hd_type", ""),
        user_role=user_profile.get("user_role", ""),
        birth_date=user_profile.get("birth_date", ""),
        birth_time=user_profile.get("birth_time", ""),
        birth_place=user_profile.get("birth_place", ""),
    )


def birth_data_minimum_for_advice(raw: str) -> bool:
    """True, если в строке есть парсибельная дата (и при желании время) для привязки совета."""
    return _extract_birth_numbers(raw or "") is not None
_USER_COLUMNS = {
    "crystals",
    "balance",
    "balance_crystals",
    "balance_energy",
    "last_free_date",
    "last_reset_date",
    "hd_report_json",
    "hd_type",
    "hd_birth_data",
    "match_partner_data",
    "energy",
    "tariff",
    "referred_by",
    "photo_daily_date",
    "photo_daily_count",
    "username",
    "persistent_memory",
    "text_daily_date",
    "text_daily_count",
    "has_paid",
    "has_pro_analysis",
    "hd_compatibility_json",
    "advice_birth_data",
    "advice_user_role",
    "last_advice_message_id",
}


def _gemini_api_key() -> str:
    return (_app_settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()


def _gemini_configured() -> bool:
    key = _gemini_api_key()
    return bool(key and not key.startswith(("your_", "ваш_")) and genai is not None)


def _openrouter_configured() -> bool:
    from services.billing.chat_pipeline import _collect_openrouter_keys

    return bool(_collect_openrouter_keys(_app_settings))


def _configure_genai() -> "genai.Client":
    """Клиент Google Gen AI SDK (только канал B, без OpenRouter)."""
    if genai is None:
        raise RuntimeError("Установите пакет google-genai для HD-отчетов и совета дня.")
    api_key = _gemini_api_key()
    if not api_key or api_key.startswith(("your_", "ваш_")):
        raise RuntimeError("Задайте GEMINI_API_KEY в .env.")
    return genai.Client(api_key=api_key)


def _extract_gemini_text(response: object) -> str:
    """Безопасно достаёт текст: ``response.text`` может бросать при safety-block."""
    try:
        text = getattr(response, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
    except Exception:  # noqa: BLE001 — property .text у google-genai
        logger.debug("Gemini response.text unavailable", exc_info=True)

    try:
        candidates = getattr(response, "candidates", None) or ()
        chunks: list[str] = []
        for cand in candidates:
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", None) or ()
            for part in parts:
                piece = getattr(part, "text", None)
                if isinstance(piece, str) and piece:
                    chunks.append(piece)
        return "".join(chunks).strip()
    except Exception:  # noqa: BLE001
        logger.debug("Gemini candidates parse failed", exc_info=True)
        return ""


def _openrouter_models_for_premium() -> list[str]:
    """OpenRouter fallback для премиум HD и совместимости."""
    models: list[str] = []
    seen: set[str] = set()

    def _add(mid: str) -> None:
        m = (mid or "").strip()
        if m and m not in seen:
            seen.add(m)
            models.append(m)

    _add("anthropic/claude-3.5-sonnet")
    _add("google/gemini-2.5-pro")
    _add("google/gemini-2.0-pro-exp-02-05:free")
    return models


def _openrouter_models_for_premium_upgrade() -> list[str]:
    """Быстрый каскад для апгрейда legacy: без Claude, короткие таймауты."""
    models: list[str] = []
    seen: set[str] = set()

    def _add(mid: str) -> None:
        m = (mid or "").strip()
        if m and m not in seen:
            seen.add(m)
            models.append(m)

    _add("google/gemini-2.5-pro")
    _add("google/gemini-2.0-pro-exp-02-05:free")
    from business_catalog import PAID_CHAT_MODEL

    _add(PAID_CHAT_MODEL)
    return models


def _openrouter_models_for_daily_advice() -> list[str]:
    """Каскад OpenRouter: Gemini через OR → lite → живые :free."""
    from business_catalog import PAID_CHAT_MODEL

    models: list[str] = []
    seen: set[str] = set()

    def _add(mid: str) -> None:
        m = (mid or "").strip()
        if m and m not in seen:
            seen.add(m)
            models.append(m)

    _add(PAID_CHAT_MODEL)
    _add("google/gemini-2.5-flash-lite")
    try:
        from services.free_models_catalog import free_cascade_from_cache

        for mid in free_cascade_from_cache()[:4]:
            _add(mid)
    except Exception:
        logger.debug("free cascade for daily advice unavailable", exc_info=True)
    for mid in (
        "deepseek/deepseek-r1-distill-llama-8b:free",
        "meta-llama/llama-3.1-8b-instruct:free",
        "google/gemma-2-9b-it:free",
    ):
        _add(mid)
    return models


async def _generate_daily_via_gemini(prompt: str) -> str:
    """Прямой Gemini SDK; пустая строка / исключение — вызывающий код уйдёт в fallback."""
    client = _configure_genai()
    errors: list[str] = []
    for model_name in _GEMINI_DAILY_MODEL_CHAIN:
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt,
                ),
                timeout=_GEMINI_DAILY_TIMEOUT_SEC,
            )
            text = _extract_gemini_text(response)
            if text:
                return text
            errors.append(f"{model_name}: empty")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini %s: совет дня недоступен: %s", model_name, exc)
            errors.append(f"{model_name}: {exc!r}")
            continue
    raise RuntimeError("gemini_unavailable: " + "; ".join(errors))


async def _generate_daily_via_openrouter(prompt: str) -> str:
    """Резерв «Совета дня», если google-genai / Gemini API недоступны."""
    from services.ai_text import ask_ai_messages

    messages = [
        {
            "role": "system",
            "content": (
                "Ты — NeuroMule 🐎⚡️. Выполни инструкцию пользователя дословно. "
                "Пиши только на русском. Без markdown (**), без HTML. "
                "Не добавляй вступлений вроде «Конечно»."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    completion = await ask_ai_messages(
        _app_settings,
        messages,
        timeout=_OPENROUTER_DAILY_TIMEOUT_SEC,
        models=_openrouter_models_for_daily_advice(),
        max_tokens=_OPENROUTER_DAILY_MAX_TOKENS,
        temperature=0.85,
    )
    text = (completion.get("content") or "").strip()
    if not text:
        raise RuntimeError("openrouter_daily_advice_empty")
    return text


async def gemini_generate_plain_text(prompt: str) -> str:
    """
    Один запрос текста к Gemini с перебором моделей (совместимость, отчёты без JSON-режима).
    Не использует OpenRouter.
    """
    client = _configure_genai()
    errors: list[str] = []
    for model_name in _GEMINI_PREMIUM_MODEL_CHAIN:
        try:
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            text = _extract_gemini_text(response)
            if text:
                return text
            errors.append(f"{model_name}: empty")
        except Exception as exc:  # noqa: BLE001 — перебор моделей по сети/API
            logger.warning("Gemini модель %s: не удалось получить текст: %s", model_name, exc)
            errors.append(f"{model_name}: {exc!r}")
            continue
    raise RuntimeError("gemini_unavailable: " + "; ".join(errors))


def _ephe_path() -> str:
    return str(_PROJECT_ROOT / "ephe")


def _require_swe():
    if swe is None:
        raise RuntimeError("Установите пакет pyswisseph для расчета совместимости.")
    swe.set_ephe_path(_ephe_path())
    return swe


def _parse_json_object(raw: str) -> dict[str, object]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        text = text.removesuffix("```").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Gemini did not return a JSON object")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Gemini JSON response is not an object")
    return parsed


def _clamp_scale(value: object, default: int = 50) -> int:
    try:
        num = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(1, min(100, num))


def _normalize_energy_scales(raw: object) -> dict[str, int]:
    data = raw if isinstance(raw, dict) else {}
    return {
        "capacity": _clamp_scale(data.get("capacity"), 50),
        "immunity": _clamp_scale(data.get("immunity"), 50),
        "scale": _clamp_scale(data.get("scale"), 50),
    }


def compute_energy_scales_from_math(math_data: dict[str, object]) -> dict[str, int]:
    """Серверный fallback шкал, если в legacy JSON их нет."""
    defined = set(_normalize_defined_center_names(list(math_data.get("defined_centers") or [])))
    open_centers = set(_normalize_defined_center_names(list(math_data.get("open_centers") or [])))
    motors = {"Сакрал", "Эго", "Корень", "Солнечное сплетение"}
    motor_count = len(defined & motors)
    capacity = min(100, max(15, 25 + motor_count * 18 + len(defined) * 4))
    immunity = min(100, max(10, 20 + len(open_centers) * 8))
    charisma = {"Горло", "G-центр", "Селезенка"}
    scale = min(100, max(10, 30 + len(defined & charisma) * 22))
    return {"capacity": capacity, "immunity": immunity, "scale": scale}


def strip_hd_markdown_for_plain(text: str) -> str:
    """Убирает markdown (** ### #) для PDF, Instagram Stories и plain-текста."""
    if not text:
        return ""
    out = str(text)
    out = re.sub(r"^#{1,6}\s*", "", out, flags=re.MULTILINE)
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", out, flags=re.DOTALL)
    out = re.sub(r"__(.+?)__", r"\1", out, flags=re.DOTALL)
    out = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", out)
    out = re.sub(r"`([^`]+)`", r"\1", out)
    out = out.replace("**", "").replace("__", "")
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _normalize_premium_report(parsed: dict[str, object]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for key in _PREMIUM_REPORT_KEYS:
        value = parsed.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Gemini JSON response is missing non-empty {key!r}")
        report[key] = value.strip()
    if len(report["fast_facts"]) > _FAST_FACTS_MAX_LEN:
        report["fast_facts"] = report["fast_facts"][: _FAST_FACTS_MAX_LEN - 1].rstrip() + "…"
    report["energy_scales"] = _normalize_energy_scales(parsed.get("energy_scales"))
    return report


def _normalize_compat_report(parsed: dict[str, object]) -> dict[str, str]:
    report: dict[str, str] = {}
    for key in _COMPAT_REPORT_KEYS:
        value = parsed.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Compatibility JSON is missing non-empty {key!r}")
        report[key] = value.strip()
    return report


def format_premium_report(report: dict[str, Any]) -> str:
    parts = []
    if report.get("fast_facts"):
        parts.append(
            "⚡ Экспресс-анализ\n" + strip_hd_markdown_for_plain(str(report["fast_facts"]))
        )
    parts.extend(
        [
            "💎 Деньги\n" + strip_hd_markdown_for_plain(str(report["money"])),
            "❤️ Отношения\n" + strip_hd_markdown_for_plain(str(report["love"])),
            "⚡️ Энергия\n" + strip_hd_markdown_for_plain(str(report["energy"])),
            "📅 План на 30 дней\n" + strip_hd_markdown_for_plain(str(report["plan"])),
        ]
    )
    return "\n\n".join(parts)


def build_hd_math_data(hd_type: str, birth_data: str) -> dict[str, object]:
    """Собирает math_data для элитного промпта и API-ответа."""
    gates: dict[str, object] = {}
    defined_set: set[str] = set()
    derived: dict[str, str] = {}
    active_channels: list[str] = []
    try:
        gates_payload = get_calculated_gates(birth_data)
        raw_gates = gates_payload.get("gates")
        if isinstance(raw_gates, dict):
            gates = raw_gates
        gate_numbers = _collect_gate_numbers(gates)
        if gate_numbers:
            active_channels = derive_active_channels(gate_numbers)
            defined_set = derive_defined_centers_from_gates(gate_numbers)
        else:
            defined_set, _ = _defined_centers_from_birth_data(birth_data)
        if birth_data.strip():
            derived = derive_hd_chart_from_birth(birth_data)
    except Exception:
        logger.debug("build_hd_math_data: ephemeris/gates unavailable", exc_info=True)
    defined_centers = sorted(defined_set)
    open_centers = [name for name in _ALL_HD_CENTER_NAMES if name not in defined_set]
    resolved_type = hd_type
    if not resolved_type or resolved_type.strip().lower() in {"не указан", "неизвестно", ""}:
        resolved_type = derived.get("hd_type") or hd_type
    definition = derive_definition_type(defined_set, active_channels)
    synthesis_pairs = build_synthesis_pairs(
        {
            "defined_centers": defined_centers,
            "open_centers": open_centers,
            "active_channels": active_channels,
        }
    )
    return {
        "hd_type": resolved_type,
        "birth_data": birth_data,
        "defined_centers": defined_centers,
        "open_centers": open_centers,
        "gates": gates,
        "profile": derived.get("profile", ""),
        "authority": derived.get("authority", ""),
        "strategy": derived.get("strategy", ""),
        "definition": definition,
        "active_channels": active_channels,
        "synthesis_pairs": synthesis_pairs,
    }


def hd_profile_metadata(math_data: dict[str, object]) -> dict[str, str | list[str]]:
    """Метаданные карты для REST API и UI."""
    defined, open_centers = _centers_from_math_data(math_data)
    return {
        "hd_type": str(math_data.get("hd_type") or ""),
        "birth_data": str(math_data.get("birth_data") or ""),
        "defined_centers": defined,
        "open_centers": open_centers,
        "strategy": str(math_data.get("strategy") or ""),
        "authority": str(math_data.get("authority") or ""),
        "profile": str(math_data.get("profile") or ""),
        "definition": str(math_data.get("definition") or ""),
    }


def hd_report_schema_version(raw: str | None) -> int:
    if not raw:
        return 0
    try:
        parsed = _parse_json_object(raw)
        if isinstance(parsed, dict):
            return int(parsed.get("schema_version") or 1)
    except Exception:
        return 0
    return 1


def is_legacy_hd_report_raw(raw: str | None) -> bool:
    """True для отчётов до elite v2 (без schema_version или placeholder fast_facts)."""
    if not raw:
        return True
    version = hd_report_schema_version(raw)
    if version < _HD_REPORT_SCHEMA_VERSION:
        return True
    try:
        parsed = _parse_json_object(raw)
        fast = str(parsed.get("fast_facts") or "").strip()
        if fast == _LEGACY_HD_REPORT_PLACEHOLDER:
            return True
    except Exception:
        return True
    return False


def premium_report_to_json(report: dict[str, Any]) -> str:
    if all(k in report for k in _PREMIUM_REPORT_KEYS) and "energy_scales" in report:
        payload: dict[str, Any] = {
            **{k: str(report[k]).strip() for k in _PREMIUM_REPORT_KEYS},
            "energy_scales": _normalize_energy_scales(report.get("energy_scales")),
        }
    else:
        payload = _normalize_premium_report(report)
    static_ref = report.get("static_reference")
    if isinstance(static_ref, dict) and static_ref:
        payload["static_reference"] = static_ref
    synthesis_meta = report.get("synthesis_meta")
    if isinstance(synthesis_meta, dict) and synthesis_meta:
        payload["synthesis_meta"] = synthesis_meta
    payload["schema_version"] = _HD_REPORT_SCHEMA_VERSION
    return json.dumps(payload, ensure_ascii=False)


def premium_report_from_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = _parse_json_object(raw)
        return _normalize_premium_report(parsed)
    except Exception:
        try:
            parsed = _parse_json_object(raw)
            legacy_keys = ("money", "love", "energy", "plan")
            if isinstance(parsed, dict) and all(parsed.get(k) for k in legacy_keys):
                legacy_report: dict[str, Any] = {k: str(parsed[k]).strip() for k in legacy_keys}
                legacy_report["fast_facts"] = str(parsed.get("fast_facts") or "").strip() or (
                    _LEGACY_HD_REPORT_PLACEHOLDER
                )
                legacy_report["energy_scales"] = _normalize_energy_scales(parsed.get("energy_scales"))
                return legacy_report
        except Exception:
            return None
        return None


def md_to_telegram_html(text: str) -> str:
    """Минимальный Markdown → Telegram HTML (** и ###)."""
    import html as html_mod

    escaped = html_mod.escape(text or "")
    escaped = re.sub(r"^### (.+)$", r"<b>\1</b>", escaped, flags=re.MULTILINE)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    return escaped


def format_hd_congrats_html(report: dict[str, str], hd_type: str, *, intro: str) -> str:
    """Текст поздравления после покупки HD-разбора."""
    import html as html_mod

    fast = report.get("fast_facts", "").strip()
    lead = intro.strip()
    if fast:
        return f"{lead}\n\n{md_to_telegram_html(fast)}"
    type_hint = html_mod.escape(hd_type.strip()) if hd_type else "Human Design"
    return f"{lead}\n\n<b>{type_hint}</b> — выбери раздел ниже или открой интерактивный разбор."


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT OR IGNORE INTO users (
                id,
                energy,
                crystals,
                balance_energy,
                balance_crystals,
                balance,
                last_reset_date,
                tariff,
                photo_daily_count
            )
            VALUES (?, 30, 0, 30, 0, 0, ?, 'Free', 0)
            """,
            (user_id, today_iso()),
        )
        await db.commit()
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()


async def ensure_modern_hd_report(
    user_id: int,
    *,
    user_name: str = "друг",
) -> tuple[dict[str, Any] | None, bool]:
    """
    Возвращает (report, upgraded).

    Legacy-отчёты (schema v1) перегенерируются через Pro-движок без повторного списания 💎,
    если в БД сохранены дата/время/город рождения.
    """
    user = await get_user(user_id)
    keys = user.keys() if hasattr(user, "keys") else ()
    raw = user["hd_report_json"] if "hd_report_json" in keys else None
    if not raw:
        return None, False
    if not is_legacy_hd_report_raw(raw):
        return premium_report_from_json(raw), False

    birth_data = (user["hd_birth_data"] or "").strip() if "hd_birth_data" in keys else ""
    if not birth_data:
        return premium_report_from_json(raw), False

    hd_type = (user["hd_type"] or "не указан") if "hd_type" in keys else "не указан"
    logger.info(
        "HD report auto-upgrade uid=%s schema v%s→v%s",
        user_id,
        hd_report_schema_version(raw),
        _HD_REPORT_SCHEMA_VERSION,
    )
    try:
        report = await asyncio.wait_for(
            generate_premium_report(
                hd_type,
                birth_data,
                user_name=user_name,
                upgrade_mode=True,
            ),
            timeout=_HD_UPGRADE_LLM_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.error("HD report upgrade LLM timeout uid=%s", user_id)
        raise RuntimeError("hd_upgrade_timeout") from None
    except Exception:
        logger.exception("HD report upgrade LLM failed uid=%s", user_id)
        raise

    math_data = build_hd_math_data(hd_type, birth_data)
    resolved_type = str(math_data.get("hd_type") or hd_type)
    await update_user(
        user_id,
        hd_report_json=premium_report_to_json(report),
        hd_type=resolved_type,
        hd_birth_data=birth_data,
        has_pro_analysis=1,
    )
    defined = math_data.get("defined_centers") or []
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda d=defined, u=user_id: generate_premium_bodygraph(list(d), u),
        )
    except Exception:
        logger.warning("bodygraph regen on HD upgrade failed uid=%s", user_id, exc_info=True)
    return report, True


async def update_user(user_id: int, **kwargs) -> None:
    if not kwargs:
        return
    unknown = set(kwargs) - _USER_COLUMNS
    if unknown:
        raise ValueError(f"Unknown users columns: {', '.join(sorted(unknown))}")
    await get_user(user_id)
    cols = ", ".join([f"{k} = ?" for k in kwargs.keys()])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {cols} WHERE id = ?", (*kwargs.values(), user_id))
        await db.commit()


async def change_user_crystals(user_id: int, delta: int) -> None:
    from services.billing.crystals_balance import add_buy_crystals

    if delta > 0:
        await add_buy_crystals(user_id, delta)
        return
    if delta < 0:
        from services.repository import try_consume_crystals

        await try_consume_crystals(user_id, -delta)


async def try_consume_crystals(user_id: int, amount: int) -> bool:
    from services.repository import try_consume_crystals as _repo_spend

    return await _repo_spend(user_id, amount)


async def generate_premium_report(
    hd_type: str,
    birth_data: str,
    *,
    user_name: str = "друг",
    upgrade_mode: bool = False,
) -> dict[str, Any]:
    """Полный HD-разбор: multi-pass Genetic Synthesis → legacy single-prompt fallback."""
    math_data = build_hd_math_data(hd_type, birth_data)
    multipass_error: Exception | None = None
    try:
        report = await _generate_premium_report_multipass(
            user_name,
            math_data,
            upgrade_mode=upgrade_mode,
        )
        logger.info("HD premium report served via multi-pass Genetic Synthesis")
        return report
    except Exception as exc:  # noqa: BLE001
        multipass_error = exc
        logger.warning("Multi-pass premium report failed, legacy single-prompt: %s", exc)

    system_prompt, user_prompt = _build_elite_premium_hd_prompt(user_name, math_data)
    errors: list[str] = []
    if multipass_error is not None:
        errors.append(f"multipass: {multipass_error!r}")
    or_models = (
        _openrouter_models_for_premium_upgrade()
        if upgrade_mode
        else _openrouter_models_for_premium()
    )
    or_timeout = (
        _OPENROUTER_PREMIUM_UPGRADE_TIMEOUT_SEC
        if upgrade_mode
        else _OPENROUTER_PREMIUM_TIMEOUT_SEC
    )

    if _gemini_configured() and not upgrade_mode:
        try:
            report = await _generate_premium_via_gemini(system_prompt, user_prompt)
            report["energy_scales"] = compute_energy_scales_from_math(math_data)
            logger.info("HD premium report served via Gemini Pro chain (legacy)")
            return report
        except Exception as gemini_exc:  # noqa: BLE001
            logger.warning("Gemini premium report failed, trying OpenRouter: %s", gemini_exc)
            errors.append(f"gemini: {gemini_exc!r}")
    elif genai is None:
        logger.info(
            "Пакет google-genai не установлен — полный разбор через OpenRouter. "
            "На VDS: pip install 'google-genai>=1.0'"
        )
        errors.append("google-genai_missing")
    elif not _openrouter_configured():
        raise RuntimeError("hd_premium_unavailable: задайте GEMINI_API_KEY или OPENROUTER_API_KEY")
    else:
        logger.info("GEMINI_API_KEY не задан — полный разбор сразу через OpenRouter Pro chain")

    try:
        report = await _generate_premium_via_openrouter(
            system_prompt,
            user_prompt,
            models=or_models,
            timeout=or_timeout,
        )
        report["energy_scales"] = compute_energy_scales_from_math(math_data)
        logger.info("HD premium report served via OpenRouter (legacy)")
        return report
    except Exception as or_exc:  # noqa: BLE001
        logger.exception("OpenRouter premium report fallback failed")
        errors.append(f"openrouter: {or_exc!r}")

    raise RuntimeError("hd_premium_unavailable: " + "; ".join(errors))


def _parse_premium_report_from_llm(raw: str) -> dict[str, Any]:
    parsed = _parse_json_object(raw)
    return _normalize_premium_report(parsed)


def _parse_compat_report_from_llm(raw: str) -> dict[str, str]:
    parsed = _parse_json_object(raw)
    return _normalize_compat_report(parsed)


async def _generate_premium_via_gemini(system_prompt: str, user_prompt: str) -> dict[str, str]:
    client = _configure_genai()
    errors: list[str] = []
    gen_cfg = {
        "response_mime_type": "application/json",
        "max_output_tokens": _HD_PREMIUM_MAX_OUTPUT_TOKENS,
        "system_instruction": system_prompt,
    }
    for model_name in _GEMINI_PREMIUM_MODEL_CHAIN:
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=gen_cfg,
                ),
                timeout=_GEMINI_PREMIUM_TIMEOUT_SEC,
            )
            report = _parse_premium_report_from_llm(_extract_gemini_text(response))
            logger.info("HD premium Gemini model=%s", model_name)
            return report
        except Exception as exc_json:  # noqa: BLE001
            logger.warning(
                "Gemini %s: JSON-режим или разбор не удались, пробуем обычный ответ: %s",
                model_name,
                exc_json,
            )
            errors.append(f"{model_name}(json): {exc_json!r}")
            try:
                combined = f"{system_prompt}\n\n---\n\n{user_prompt}"
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=model_name,
                        contents=combined,
                        config={"max_output_tokens": _HD_PREMIUM_MAX_OUTPUT_TOKENS},
                    ),
                    timeout=_GEMINI_PREMIUM_TIMEOUT_SEC,
                )
                return _parse_premium_report_from_llm(_extract_gemini_text(response))
            except Exception as exc_plain:  # noqa: BLE001
                errors.append(f"{model_name}(plain): {exc_plain!r}")
                continue
    raise RuntimeError("gemini_unavailable: " + "; ".join(errors))


async def _generate_premium_via_openrouter(
    system_prompt: str,
    user_prompt: str,
    *,
    models: list[str] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    from services.ai_text import ask_ai_messages

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    completion = await ask_ai_messages(
        _app_settings,
        messages,
        timeout=timeout if timeout is not None else _OPENROUTER_PREMIUM_TIMEOUT_SEC,
        models=models or _openrouter_models_for_premium(),
        max_tokens=_HD_PREMIUM_MAX_OUTPUT_TOKENS,
        temperature=0.7,
        response_format={"type": "json_object"},
    )
    text = (completion.get("content") or "").strip()
    if not text:
        raise RuntimeError("openrouter_premium_report_empty")
    return _parse_premium_report_from_llm(text)


async def generate_hd_report(hd_type: str, birth_data: str) -> str:
    report = await generate_premium_report(hd_type, birth_data)
    return format_premium_report(report)


def parse_match_request(raw: str) -> tuple[str | None, str]:
    text = (raw or "").strip()
    if not text:
        return None, ""
    lower = text.lower()
    markers = ("партнер:", "партнёр:", "второй:", "2:", "человек 2:")
    for marker in markers:
        idx = lower.find(marker)
        if idx != -1:
            return text[:idx].strip() or None, text[idx + len(marker) :].strip()
    return None, text


def _extract_birth_numbers(raw: str) -> tuple[int, int, int, int, int] | None:
    import re

    match = re.search(
        r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})(?:\D+(\d{1,2})[:.](\d{2}))?",
        raw or "",
    )
    if not match:
        return None
    day, month, year = (int(match.group(i)) for i in (1, 2, 3))
    hour = int(match.group(4) or 12)
    minute = int(match.group(5) or 0)
    return year, month, day, hour, minute


def calculate_bodygraph_snapshot(birth_data: str) -> dict[str, float | str]:
    sw = _require_swe()
    parts = _extract_birth_numbers(birth_data)
    if parts is None:
        raise ValueError("Не удалось найти дату рождения в формате ДД.ММ.ГГГГ и время ЧЧ:ММ.")
    year, month, day, hour, minute = parts
    jd = sw.julday(year, month, day, hour + minute / 60.0)
    bodies = {
        "sun": sw.SUN,
        "moon": sw.MOON,
        "mercury": sw.MERCURY,
        "venus": sw.VENUS,
        "mars": sw.MARS,
        "jupiter": sw.JUPITER,
        "saturn": sw.SATURN,
        "uranus": sw.URANUS,
        "neptune": sw.NEPTUNE,
        "pluto": sw.PLUTO,
    }
    snapshot: dict[str, float | str] = {"birth_data": birth_data.strip(), "julian_day": jd}
    for name, planet in bodies.items():
        pos, _flags = sw.calc_ut(jd, planet)
        snapshot[name] = round(float(pos[0]), 6)
    return snapshot


def _longitude_to_gate(longitude: float) -> dict[str, int | float]:
    gate_width = 360.0 / 64.0
    line_width = gate_width / 6.0
    normalized = longitude % 360.0
    gate_index = int(normalized // gate_width)
    position_in_gate = normalized - gate_index * gate_width
    line = int(position_in_gate // line_width) + 1
    return {
        "gate": _HD_GATE_SEQUENCE[gate_index],
        "line": min(line, 6),
        "longitude": round(normalized, 6),
    }


def get_calculated_gates(birth_data: str) -> dict[str, object]:
    snapshot = calculate_bodygraph_snapshot(birth_data)
    gates: dict[str, object] = {}
    for key, value in snapshot.items():
        if key in {"birth_data", "julian_day"} or not isinstance(value, float):
            continue
        gates[key] = _longitude_to_gate(value)
    return {
        "birth_data": snapshot["birth_data"],
        "julian_day": snapshot["julian_day"],
        "gates": gates,
    }


_HD_DESIGN_DAYS_OFFSET = 88.0

_HD_TYPE_STRATEGIES: dict[str, str] = {
    "генератор": "Ждать отклик и отвечать телом",
    "манифестирующий генератор": "Ждать отклик, затем информировать и действовать",
    "манифестор": "Информировать окружающих перед действием",
    "проектор": "Ждать приглашения и признания",
    "рефлектор": "Ждать лунный цикл (28 дней) для важных решений",
}


def _infer_hd_type_from_centers(defined: set[str]) -> str:
    if not defined:
        return "Рефлектор"
    motors_throat = {"Эго", "Солнечное сплетение", "Корень"}
    if "Сакрал" in defined:
        if "Горло" in defined and defined & motors_throat:
            return "Манифестирующий Генератор"
        return "Генератор"
    if "Горло" in defined and defined & motors_throat:
        return "Манифестор"
    return "Проектор"


def _infer_authority_from_centers(defined: set[str]) -> str:
    if not defined:
        return "Лунный"
    if "Солнечное сплетение" in defined:
        return "Эмоциональный"
    if "Сакрал" in defined:
        return "Сакральный"
    if "Селезенка" in defined:
        return "Селезеночный"
    if "Эго" in defined:
        return "Эго"
    if "G-центр" in defined:
        return "Самопроецируемый"
    return "Внутренний (уточняется по каналам)"


def _strategy_for_hd_type(hd_type: str) -> str:
    key = (hd_type or "").strip().lower()
    for pattern, strategy in _HD_TYPE_STRATEGIES.items():
        if pattern in key:
            return strategy
    return "Следовать стратегии своего типа"


def _sun_line_for_birth_data(birth_data: str, *, design: bool = False) -> int:
    sw = _require_swe()
    parts = _extract_birth_numbers(birth_data)
    if parts is None:
        raise ValueError("invalid_birth_data")
    year, month, day, hour, minute = parts
    jd = sw.julday(year, month, day, hour + minute / 60.0)
    if design:
        jd -= _HD_DESIGN_DAYS_OFFSET
    pos, _flags = sw.calc_ut(jd, sw.SUN)
    line = _longitude_to_gate(float(pos[0])).get("line", 1)
    return int(line) if isinstance(line, int) else 1


def derive_hd_chart_from_birth(birth_data: str) -> dict[str, str]:
    """Swiss Ephemeris: тип, профиль, авторитет, стратегия для легенды PDF и Stories."""
    defined, _warn = _defined_centers_from_birth_data(birth_data)
    hd_type = _infer_hd_type_from_centers(defined)
    authority = _infer_authority_from_centers(defined)
    strategy = _strategy_for_hd_type(hd_type)
    profile = ""
    try:
        line_p = _sun_line_for_birth_data(birth_data, design=False)
        line_d = _sun_line_for_birth_data(birth_data, design=True)
        profile = f"{line_p}/{line_d}"
    except Exception:
        logger.debug("HD profile lines unavailable", exc_info=True)
    return {
        "hd_type": hd_type,
        "profile": profile,
        "authority": authority,
        "strategy": strategy,
    }


def resolve_hd_math_data(hd_type: str, birth_data: str) -> dict[str, object]:
    """Канонический math_data с авто-расчётом типа/профиля, если в БД «не указан»."""
    return build_hd_math_data(hd_type, birth_data)


def calculate_composite(first_birth_data: str, second_birth_data: str) -> dict[str, object]:
    first = calculate_bodygraph_snapshot(first_birth_data)
    second = calculate_bodygraph_snapshot(second_birth_data)
    composite: dict[str, float] = {}
    for key, value in first.items():
        if key in {"birth_data", "julian_day"} or not isinstance(value, float):
            continue
        other = second.get(key)
        if isinstance(other, float):
            delta = abs(value - other)
            composite[key] = round(min(delta, 360 - delta), 6)
    return {"first": first, "second": second, "composite_degrees": composite}


async def generate_daily_forecast(
    user_profile: DailyAdviceUserProfile,
    *,
    current_cta_text: str,
) -> str:
    """Совет дня: Gemini SDK → при сбое/отсутствии пакета — OpenRouter."""
    prompt = build_daily_advice_prompt(user_profile, current_cta_text=current_cta_text)
    errors: list[str] = []

    if _gemini_configured():
        try:
            return await _generate_daily_via_gemini(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini daily advice failed, trying OpenRouter: %s", exc)
            errors.append(f"gemini: {exc!r}")
    elif genai is None:
        logger.info(
            "Пакет google-genai не установлен — «Совет дня» через OpenRouter. "
            "На VDS: pip install 'google-genai>=1.0'"
        )
        errors.append("google-genai_missing")
    elif not _openrouter_configured():
        raise RuntimeError("daily_advice_unavailable: задайте GEMINI_API_KEY или OPENROUTER_API_KEY")

    try:
        text = await _generate_daily_via_openrouter(prompt)
        logger.info("daily advice served via OpenRouter fallback")
        return text
    except Exception as exc:  # noqa: BLE001
        logger.exception("OpenRouter daily advice fallback failed")
        errors.append(f"openrouter: {exc!r}")

    raise RuntimeError("daily_advice_unavailable: " + "; ".join(errors))


async def generate_daily_advice(
    user_profile: DailyAdviceUserProfile,
    *,
    current_cta_text: str,
) -> str:
    """Алиас для совместимости импортов."""
    return await generate_daily_forecast(user_profile, current_cta_text=current_cta_text)


def today_iso() -> str:
    return date.today().isoformat()


def _find_pdf_font() -> str | None:
    candidates = [
        str(_PROJECT_ROOT / "fonts" / "Roboto-Regular.ttf"),
        os.getenv("HD_PDF_FONT_PATH", "").strip(),
        str(_PROJECT_ROOT / "fonts" / "DejaVuSans.ttf"),
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for item in candidates:
        if item and Path(item).exists():
            return item
    return None


def _register_pdf_font() -> str:
    if pdfmetrics is None or TTFont is None:
        raise RuntimeError("Установите пакет reportlab для PDF-отчетов.")
    font_path = _find_pdf_font()
    if not font_path:
        return "Helvetica"
    registered = pdfmetrics.getRegisteredFontNames()
    if _PDF_FONT_NAME not in registered:
        pdfmetrics.registerFont(TTFont(_PDF_FONT_NAME, font_path))
    return _PDF_FONT_NAME


_GATE_TO_CENTER = {
    64: "Голова",
    61: "Голова",
    63: "Голова",
    47: "Аджна",
    24: "Аджна",
    4: "Аджна",
    17: "Аджна",
    43: "Аджна",
    11: "Аджна",
    62: "Горло",
    23: "Горло",
    56: "Горло",
    35: "Горло",
    12: "Горло",
    45: "Горло",
    33: "Горло",
    31: "Горло",
    8: "Горло",
    20: "Горло",
    16: "Горло",
    1: "G-центр",
    13: "G-центр",
    25: "G-центр",
    46: "G-центр",
    2: "G-центр",
    15: "G-центр",
    10: "G-центр",
    7: "G-центр",
    21: "Эго",
    51: "Эго",
    26: "Эго",
    40: "Эго",
    48: "Селезенка",
    57: "Селезенка",
    44: "Селезенка",
    50: "Селезенка",
    32: "Селезенка",
    28: "Селезенка",
    18: "Селезенка",
    5: "Сакрал",
    14: "Сакрал",
    29: "Сакрал",
    59: "Сакрал",
    9: "Сакрал",
    34: "Сакрал",
    27: "Сакрал",
    42: "Сакрал",
    3: "Сакрал",
    6: "Солнечное сплетение",
    37: "Солнечное сплетение",
    22: "Солнечное сплетение",
    36: "Солнечное сплетение",
    30: "Солнечное сплетение",
    55: "Солнечное сплетение",
    49: "Солнечное сплетение",
    53: "Корень",
    60: "Корень",
    52: "Корень",
    19: "Корень",
    39: "Корень",
    41: "Корень",
    58: "Корень",
    38: "Корень",
    54: "Корень",
}

# 36 каналов IHDS: обе ворота должны быть активны, чтобы канал считался complete.
_HD_CHANNELS_RAW: tuple[tuple[int, int], ...] = (
    (1, 8),
    (2, 14),
    (3, 60),
    (4, 63),
    (5, 15),
    (6, 59),
    (7, 31),
    (9, 52),
    (10, 20),
    (10, 34),
    (10, 57),
    (11, 56),
    (12, 22),
    (13, 33),
    (16, 48),
    (17, 62),
    (18, 58),
    (19, 49),
    (20, 34),
    (20, 57),
    (21, 45),
    (23, 43),
    (24, 61),
    (25, 51),
    (26, 44),
    (27, 50),
    (28, 38),
    (29, 46),
    (30, 41),
    (32, 54),
    (34, 57),
    (35, 36),
    (37, 40),
    (39, 55),
    (42, 53),
    (47, 64),
)


def _format_hd_channel(g1: int, g2: int) -> str:
    low, high = sorted((g1, g2))
    return f"{low}-{high}"


def _collect_gate_numbers(gates: object) -> set[int]:
    nums: set[int] = set()
    if not isinstance(gates, dict):
        return nums
    for payload in gates.values():
        if isinstance(payload, dict):
            gate = payload.get("gate")
            if isinstance(gate, int):
                nums.add(gate)
    return nums


def derive_active_channels(gate_numbers: set[int]) -> list[str]:
    """Верифицированные complete-каналы по набору активных ворот."""
    if not gate_numbers:
        return []
    active: list[str] = []
    seen: set[str] = set()
    for g1, g2 in _HD_CHANNELS_RAW:
        if g1 in gate_numbers and g2 in gate_numbers:
            label = _format_hd_channel(g1, g2)
            if label not in seen:
                seen.add(label)
                active.append(label)
    return sorted(active)


def derive_defined_centers_from_gates(gate_numbers: set[int]) -> set[str]:
    """IHDS: центр defined только если в нём замкнут хотя бы один complete-канал."""
    defined: set[str] = set()
    for ch in derive_active_channels(gate_numbers):
        g1, g2 = (int(part) for part in ch.split("-", 1))
        for gate in (g1, g2):
            center = _GATE_TO_CENTER.get(gate)
            if center:
                defined.add(center)
    return defined


def derive_definition_type(defined_centers: set[str], active_channels: list[str]) -> str:
    """Single / Split / Triple / Quad по числу связных компонент defined-графа."""
    if not defined_centers:
        return "None"
    adjacency: dict[str, set[str]] = {center: set() for center in defined_centers}
    for ch in active_channels:
        parts = ch.split("-", 1)
        if len(parts) != 2:
            continue
        g1, g2 = int(parts[0]), int(parts[1])
        c1 = _GATE_TO_CENTER.get(g1)
        c2 = _GATE_TO_CENTER.get(g2)
        if c1 in defined_centers and c2 in defined_centers:
            adjacency[c1].add(c2)
            adjacency[c2].add(c1)
    visited: set[str] = set()
    components = 0
    for center in defined_centers:
        if center in visited:
            continue
        components += 1
        stack = [center]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            stack.extend(adjacency[node] - visited)
    if components <= 1:
        return "Single"
    if components == 2:
        return "Split"
    if components == 3:
        return "Triple"
    return "Quad"


def build_synthesis_pairs(math_data: dict[str, object]) -> list[dict[str, object]]:
    """Пары open_center × defined_motors для модульного Genetic Synthesis."""
    defined, open_centers = _centers_from_math_data(math_data)
    defined_set = set(defined)
    motors = sorted(defined_set & _HD_MOTOR_CENTERS)
    active_channels = [
        str(ch).strip()
        for ch in (math_data.get("active_channels") or [])
        if str(ch).strip()
    ]
    pairs: list[dict[str, object]] = []
    for open_center in open_centers:
        channel_hints: list[str] = []
        for ch in active_channels:
            parts = ch.split("-", 1)
            if len(parts) != 2:
                continue
            g1, g2 = int(parts[0]), int(parts[1])
            centers_in_channel = {
                _GATE_TO_CENTER.get(g1),
                _GATE_TO_CENTER.get(g2),
            } - {None}
            if open_center in centers_in_channel or centers_in_channel & defined_set:
                channel_hints.append(ch)
        anchors: list[str] = list(motors)
        if channel_hints:
            anchors.extend(f"канал {hint}" for hint in channel_hints)
        if not anchors:
            anchors = ["определённые моторы отсутствуют — опирайся только на факты карты"]
        pairs.append(
            {
                "open_center": open_center,
                "anchors": anchors,
                "channel_hints": channel_hints,
            }
        )
    return pairs


_HD_BODYGRAPH_TEMPLATE_PATH = _PROJECT_ROOT / "assets" / "hd_blank_template.png"
_HD_BODYGRAPH_OUTPUT_DIR = _PROJECT_ROOT / "tmp"
_HD_GLOW_COLOR = (139, 92, 246, 70)
_HD_FILL_COLOR = (139, 92, 246, 180)
_HD_OUTLINE_COLOR = (255, 255, 255, 255)
_HD_GLOW_BLUR_RADIUS = 15
_HD_GLOW_STROKE_WIDTH = 25
_HD_FILL_OUTLINE_WIDTH = 2

# Полигоны (x, y) под шаблон 1024×1024 — стеклянный силуэт, центр кадра ~512.
center_coordinates: dict[str, tuple[tuple[int, int], ...]] = {
    "Голова": ((512, 88), (440, 188), (584, 188)),
    "Аджна": ((445, 198), (579, 198), (512, 278)),
    "Горло": ((465, 288), (559, 288), (559, 348), (465, 348)),
    "G-центр": ((512, 368), (578, 425), (512, 482), (446, 425)),
    "Эго": ((558, 408), (648, 448), (558, 488)),
    "Селезенка": ((498, 478), (358, 548), (498, 618)),
    "Солнечное сплетение": ((526, 478), (666, 548), (526, 618)),
    "Сакрал": ((458, 608), (566, 608), (566, 708), (458, 708)),
    "Корень": ((458, 732), (566, 732), (566, 852), (458, 852)),
}

_CENTER_NAME_ALIASES: dict[str, str] = {
    "g-центр": "G-центр",
    "g центр": "G-центр",
    "джи-центр": "G-центр",
    "джи центр": "G-центр",
    "солнечное сплетение": "Солнечное сплетение",
    "селезенка": "Селезенка",
    "голова": "Голова",
    "аджна": "Аджна",
    "горло": "Горло",
    "эgo": "Эго",
    "эго": "Эго",
    "сакрал": "Сакрал",
    "корень": "Корень",
}


def _normalize_defined_center_names(defined_centers: list[str]) -> list[str]:
    out: list[str] = []
    for raw in defined_centers or []:
        name = (raw or "").strip()
        if not name:
            continue
        canonical = _CENTER_NAME_ALIASES.get(name.lower(), name)
        if canonical in center_coordinates and canonical not in out:
            out.append(canonical)
    return out


def generate_premium_bodygraph(defined_centers: list, uid: int) -> str:
    """
    Премиальный бодиграф: неоновое свечение + полупрозрачная заливка поверх 3D-шаблона.

    Returns:
        Относительный путь ``tmp/ready_hd_{uid}.png`` от корня проекта.
    """
    if Image is None or ImageDraw is None or ImageFilter is None:
        raise RuntimeError("Установите пакет Pillow для генерации бодиграфа.")

    template_path = _HD_BODYGRAPH_TEMPLATE_PATH
    if not template_path.is_file():
        raise RuntimeError(f"HD bodygraph template not found: {template_path}")

    active = _normalize_defined_center_names(list(defined_centers))
    base = Image.open(template_path).convert("RGBA")
    width, height = base.size

    glow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer)
    for center_name in active:
        polygon = center_coordinates[center_name]
        glow_draw.polygon(
            polygon,
            fill=_HD_GLOW_COLOR,
            outline=_HD_GLOW_COLOR,
            width=_HD_GLOW_STROKE_WIDTH,
        )
    glow_blurred = glow_layer.filter(ImageFilter.GaussianBlur(radius=_HD_GLOW_BLUR_RADIUS))

    fill_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    fill_draw = ImageDraw.Draw(fill_layer)
    for center_name in active:
        polygon = center_coordinates[center_name]
        fill_draw.polygon(polygon, fill=_HD_FILL_COLOR)
        fill_draw.polygon(
            polygon,
            outline=_HD_OUTLINE_COLOR,
            width=_HD_FILL_OUTLINE_WIDTH,
        )

    composed = Image.alpha_composite(base, glow_blurred)
    composed = Image.alpha_composite(composed, fill_layer)

    os.makedirs(str(_HD_BODYGRAPH_OUTPUT_DIR), exist_ok=True)
    out_path = _HD_BODYGRAPH_OUTPUT_DIR / f"ready_hd_{uid}.png"
    composed.save(out_path, format="PNG")
    return f"tmp/ready_hd_{uid}.png"


def _defined_centers_from_birth_data(birth_data: str | None) -> tuple[set[str], str | None]:
    if not birth_data:
        return set(), "Данные рождения не переданы для схемы."
    try:
        gates = get_calculated_gates(birth_data)["gates"]
    except Exception as exc:
        return set(), f"Схема не рассчитана: {exc}"
    gate_numbers = _collect_gate_numbers(gates)
    if gate_numbers:
        return derive_defined_centers_from_gates(gate_numbers), None
    defined: set[str] = set()
    if isinstance(gates, dict):
        for value in gates.values():
            if isinstance(value, dict):
                gate = value.get("gate")
                if isinstance(gate, int) and gate in _GATE_TO_CENTER:
                    defined.add(_GATE_TO_CENTER[gate])
    return defined, None


_ALL_HD_CENTER_NAMES: tuple[str, ...] = tuple(center_coordinates.keys())

_ELITE_HD_BANNED_MARKERS: tuple[str, ...] = (
    "проживан",
    "корректност",
    "вибраци",
    "аур",
    "карма",
    "космос",
    "нейтрино",
    "обуславливан",
    "вселенн",
    "астрал",
    "chakra",
    "чakra",
    "эфир",
    "судьб",
    "karm",
)

_GENETIC_SYNTHESIS_BANNED_MARKERS: tuple[str, ...] = _ELITE_HD_BANNED_MARKERS


def _centers_from_math_data(math_data: dict) -> tuple[list[str], list[str]]:
    """Возвращает (defined_centers, open_centers) из math_data или расчёта по birth_data."""
    raw_defined = math_data.get("defined_centers")
    if raw_defined is None and math_data.get("defined"):
        raw_defined = math_data.get("defined")
    defined = _normalize_defined_center_names(list(raw_defined or []))

    birth_data = str(math_data.get("birth_data") or "").strip()
    if not defined and birth_data:
        defined_set, _ = _defined_centers_from_birth_data(birth_data)
        defined = sorted(defined_set)

    raw_open = math_data.get("open_centers")
    if raw_open is None and math_data.get("open"):
        raw_open = math_data.get("open")
    if raw_open is not None:
        open_centers = _normalize_defined_center_names(list(raw_open or []))
    else:
        open_centers = [name for name in _ALL_HD_CENTER_NAMES if name not in set(defined)]
    return defined, open_centers


def _format_gates_block(gates: object) -> str:
    if not isinstance(gates, dict) or not gates:
        return "Ворота: не переданы (опирайся только на списки центров)."
    lines: list[str] = []
    for planet, payload in sorted(gates.items(), key=lambda item: str(item[0])):
        if not isinstance(payload, dict):
            continue
        gate = payload.get("gate")
        line = payload.get("line")
        if gate is None:
            continue
        center = _GATE_TO_CENTER.get(int(gate), "?")
        line_suffix = f".{line}" if line is not None else ""
        lines.append(f"- {planet}: ворота {gate}{line_suffix} → центр «{center}»")
    return "Активные ворота (расчёт Swiss Ephemeris, не пересчитывай):\n" + (
        "\n".join(lines) if lines else "- нет данных"
    )


def _hd_tone_profile(hd_type: str) -> str:
    """Динамический Tone of Voice под тип карты."""
    normalized = (hd_type or "").strip().lower()
    if any(token in normalized for token in ("манифест", "генератор", "мг", "м.г.")):
        return (
            "СТИЛЬ РЕЧИ: жёсткий бизнес-ментор. Короткие императивы, операционка, делегирование, "
            "скорость решений, KPI, дисциплина исполнения. Без сюсюканья — уважительная прямота."
        )
    if any(token in normalized for token in ("проектор", "рефлектор")):
        return (
            "СТИЛЬ РЕЧИ: глубокий психоаналитик. Границы, распознавание паттернов, мудрость через "
            "наблюдение, телесные сигналы, циклы ожидания. Мягкая точность без мистики."
        )
    return (
        "СТИЛЬ РЕЧИ: премиальный ICF-коуч — конкретика, ответственность клиента, измеримые шаги."
    )


_ELITE_HD_FEW_SHOT = (
    "ПРИМЕР ПЛОТНОСТИ И СТИЛЯ (few-shot, не копируй факты — только плотность и тон):\n"
    '{"fast_facts": "⚡ Главный баг прошивки: доказываешь ценность через переработку. '
    '💼 Триггер больших денег: продавать только после телесного «да». '
    '🔋 Идеальная перезагрузка: сон без будильника + прогулка без цели.", '
    '"money": "Боль\\nТы берёшь проекты из страха «останусь без денег».\\n\\n'
    'Что делать\\n**Неделя 1:** веди список откликов тела перед каждым «да».", '
    '"love": "Боль\\nТы читаешь ожидания партнёра и теряешь себя в роли «удобного».", '
    '"energy": "Боль\\nЖмёшь газ, когда Сакрал уже пуст.", '
    '"plan": "Дни 1–5\\nОтслеживай сигнал тела перед решениями."}'
)

_ELITE_HD_SERVER_MATH_MANDATE = (
    "Ты получаешь точные, математически рассчитанные на сервере данные бодиграфа пользователя "
    "(тип, профиль, закрашенные и открытые центры). Тебе категорически ЗАПРЕЩЕНО самостоятельно "
    "рассчитывать, угадывать или изменять тип личности пользователя. Ты должен строго взять "
    "переданный тип личности и провести его глубокую коучинговую расшифровку по нашему JSON-контракту."
)


def _build_elite_premium_hd_prompt(user_name: str, math_data: dict) -> tuple[str, str]:
    """
    Элитный промпт полного HD-разбора: ICF-коучинг без эзотерики, строго по math_data.

    ``math_data`` ожидает ключи (все опциональны, кроме фактов для анти-галлюцинаций):
        - ``hd_type`` — Манифестор / Генератор / Проектор / Рефлектор (не менять!)
        - ``birth_data`` — дата, время, город одной строкой
        - ``defined_centers`` / ``open_centers`` — списки имён центров
        - ``gates`` — словарь из ``get_calculated_gates()["gates"]``
        - ``strategy``, ``authority``, ``profile`` — если уже известны из расчёта/БД

    Returns:
        (system_prompt, user_prompt) для двухturn-запроса к LLM.
    """
    name = (user_name or "").strip() or "друг"
    data = math_data if isinstance(math_data, dict) else {}

    hd_type = str(data.get("hd_type") or data.get("type") or "").strip() or "не указан"
    birth_data = str(data.get("birth_data") or "").strip() or "не указаны"
    strategy = str(data.get("strategy") or "").strip()
    authority = str(data.get("authority") or "").strip()
    profile = str(data.get("profile") or "").strip()

    defined_centers, open_centers = _centers_from_math_data(data)
    defined_line = ", ".join(defined_centers) if defined_centers else "не переданы — не выдумывай"
    open_line = ", ".join(open_centers) if open_centers else "не переданы — не выдумывай"
    gates_block = _format_gates_block(data.get("gates"))
    tone_block = _hd_tone_profile(hd_type)

    banned = ", ".join(f"«{word}»" for word in _ELITE_HD_BANNED_MARKERS[:8])

    system_prompt = (
        "Ты — сертифицированный коуч уровня ICF и практик Human Design для NeuroMule. "
        "Пишешь премиальный персональный разбор: глубинная психология, прикладная механика тела и решений. "
        "Без эзотерической воды — только поведение, паттерны, границы, деньги, отношения, энергия.\n\n"
        f"{tone_block}\n\n"
        f"{_ELITE_HD_SERVER_MATH_MANDATE}\n\n"
        "ЖЁСТКИЕ ЗАПРЕТЫ:\n"
        f"- Не используй: {banned}, «вибрации», «карма», «космос», «вселенная посылает», "
        "«астрал», «судьба», «предназначение-сверху», «нейтрино».\n"
        "- Не выдумывай тип, стратегию, авторитет, профиль, ворота или центры — только факты из user-блока.\n"
        "- ЗАПРЕЩЕНО путать типы и менять списки определённых/открытых центров.\n"
        "- Определённые центры — устойчивые ресурсы; открытые — зоны обучаемости и риска «Ложного Я».\n"
        "- Обращайся к клиенту на «ты».\n"
        "- Ответ — ТОЛЬКО чистый JSON без markdown-обёрток ```.\n\n"
        f"{_ELITE_HD_FEW_SHOT}\n\n"
        "ФОРМАТ ОТВЕТА (строго один JSON-объект, ключи только на английском):\n"
        '{"fast_facts": "...", "money": "...", "love": "...", "energy": "...", "plan": "...", '
        '"energy_scales": {"capacity": 72, "immunity": 55, "scale": 81}}\n'
        "- fast_facts: до 300 символов, три строки в одном поле: "
        "'⚡ Главный баг прошивки: …', '💼 Триггер больших денег: …', '🔋 Идеальная перезагрузка: …'. "
        "Переводи номера каналов/ворот в понятные психологические суперсилы — БЕЗ сухих кодов "
        "вида '34-20', '19-49', 'Gate 57'. Без ### и # — только plain text, эмодзи и **жирный**.\n"
        "- energy_scales: три целых числа 1–100 — capacity (ёмкость ауры по моторам), "
        "immunity (стойкость к чужому мнению по открытым центрам), scale (индекс харизмы/влияния).\n"
        "- money, love, energy: plain text с подзаголовками «Боль» и «Что делать». "
        "Для **жирного акцента** используй только парные **звёздочки** (без ### и #). "
        "КАЖДЫЙ раздел начинается с честной психологической боли из-за Ложного Я этой механики. "
        "Объём каждого раздела — от 2500 до 6000 символов; суммарно отчёт должен давать "
        "30–40 страниц PDF при верстке.\n"
        "- ГЕНЕТИЧЕСКИЙ СИНТЕЗ (обязательно): каждый открытый центр описывай только в жёсткой "
        "связке с определёнными моторами и каналами клиента — например, открытое Эго на фоне "
        "определённого Сакрала, открытая Голова при определённом Корне. Никаких абстрактных "
        "описаний центров «в вакууме» — только персональные паттерны этой карты.\n"
        "- plan: plain text план на 30 дней (блоки 1–5 / 6–15 / 16–30) с действиями и метриками. "
        "Допускается **жирный** через **звёздочки**, без ###.\n"
        "Каждый раздел — плотный, без воды; в каждом есть ответ «что делать дальше»."
    )

    user_prompt = (
        f"Клиент: {name}. Обращайся к {name} на «ты».\n\n"
        "МАТЕМАТИЧЕСКИ ЗАФИКСИРОВАННЫЕ ФАКТЫ (истина, не оспаривай и не дополняй):\n"
        f"- Тип HD: {hd_type}\n"
        f"- Дата/время/место рождения: {birth_data}\n"
        f"- Стратегия: {strategy or 'не передана'}\n"
        f"- Авторитет: {authority or 'не передан'}\n"
        f"- Профиль: {profile or 'не передан'}\n"
        f"- Определённые (закрашенные) центры: {defined_line}\n"
        f"- Открытые (незакрашенные) центры: {open_line}\n"
        f"- {gates_block}\n\n"
        "Сгенерируй JSON-разбор, ювелирно согласованный с определёнными и открытыми центрами выше. "
        "Если центр открыт — не описывай его как постоянный ресурс. "
        "Если центр определён — не называй его зоной уязвимости из-за «отсутствия энергии». "
        "Используй переданный тип личности дословно во всех рекомендациях — без пересчёта и без замены."
    )
    return system_prompt, user_prompt


_GENETIC_SYNTHESIS_FEW_SHOT = (
    "ПРИМЕР ПЛОТНОСТИ JSON (few-shot — не копируй факты, только структуру и тон):\n"
    '{"synthesis_anchor": "Открытое Эго × определённый Сакрал, профиль 3/5, сфера money.", '
    '"client_pain": "Ты соглашаешься на сделки, чтобы доказать ценность, и выгораешь.", '
    '"false_self_pattern": "Ум подменяет уязвимость Эго перегрузкой Сакрала.", '
    '"body_signal": "Сжатие в груди и ускоренное дыхание перед подписанием договора.", '
    '"reflective_questions": ["Что меняется в теле, если отложить «да» на 24 часа?", '
    '"Где я доказываю ценность вместо того, чтобы назвать цену?", '
    '"Какой минимальный шаг даст мне данные без обязательства?"], '
    '"experiments": [{"timeframe": "days_1-5", "action": "Перед каждым финансовым «да» '
    'записывай телесный сигнал", "metric": "Количество решений и телесный отклик", '
    '"success_criteria": "5 записей с различимым телесным паттерном"}]}'
)


def _format_active_channels_line(active_channels: object) -> str:
    if isinstance(active_channels, list) and active_channels:
        return ", ".join(str(ch).strip() for ch in active_channels if str(ch).strip())
    return "нет complete-каналов в переданных данных — не выдумывай"


def _format_synthesis_anchors(anchors: object) -> str:
    if isinstance(anchors, list) and anchors:
        return ", ".join(str(item).strip() for item in anchors if str(item).strip())
    return "не переданы — не выдумывай"


def _format_energy_scales_line(energy_scales: dict[str, int]) -> str:
    return (
        f"capacity={energy_scales.get('capacity', 50)}, "
        f"immunity={energy_scales.get('immunity', 50)}, "
        f"scale={energy_scales.get('scale', 50)}"
    )


def _build_genetic_synthesis_prompt(
    *,
    domain: str,
    math_data: dict[str, object],
    synthesis_pair: dict[str, object],
    energy_scales: dict[str, int],
) -> tuple[str, str]:
    """
    Промпт модульного Genetic Synthesis: одна open×defined пара × domain.

    Returns:
        (system_prompt, user_prompt) для LLM с temperature=0.1.
    """
    normalized_domain = (domain or "").strip().lower()
    if normalized_domain not in _GENETIC_SYNTHESIS_DOMAINS:
        raise ValueError(f"unsupported synthesis domain: {domain!r}")

    data = math_data if isinstance(math_data, dict) else {}
    pair = synthesis_pair if isinstance(synthesis_pair, dict) else {}
    open_center = str(pair.get("open_center") or "").strip() or "не передан"
    anchors = _format_synthesis_anchors(pair.get("anchors"))

    profile = str(data.get("profile") or "").strip() or "не передан"
    authority = str(data.get("authority") or "").strip() or "не передан"
    strategy = str(data.get("strategy") or "").strip() or "не передана"
    definition = str(data.get("definition") or "").strip() or "не передана"
    active_channels_line = _format_active_channels_line(data.get("active_channels"))
    scales_line = _format_energy_scales_line(energy_scales)

    banned = ", ".join(f"«{word}»" for word in _GENETIC_SYNTHESIS_BANNED_MARKERS[:10])
    domain_ru = {"money": "деньги", "love": "отношения", "energy": "энергия"}[normalized_domain]

    system_prompt = (
        "Контекст: Ты — ИИ-генератор премиального движка «Генетического Синтеза» NeuroMule HD. "
        "Твоя роль — аналитик Дизайна Человека высшей категории (IHDS-канон) и международный "
        "сертифицированный коуч (ICF). Ты создаёшь глубокую, терапевтическую книгу-инструкцию, "
        "которая сшивает параметры карты пользователя в единый жизненный нарратив.\n\n"
        "ПРАВИЛА И ОГРАНИЧЕНИЯ:\n"
        "1. ТОТАЛЬНЫЙ ЗАПРЕТ НА ГАЛЛЮЦИНАЦИИ КАНАЛОВ И ВОРОТ: Тебе запрещено упоминать, "
        "придумывать или предполагать наличие любых ворот или каналов, которых нет в списках "
        "active_channels и входных данных. Ты оперируешь только предоставленным контекстом.\n"
        f"2. ЗАПРЕТ ЭЗОТЕРИЧЕСКОГО ЖАРГОНА: Полностью исключи слова-маркеры: {banned}. "
        "Переводи терминологию IHDS на язык современной психологии и коучинга "
        "(«Ложное Я» = «Компенсаторные паттерны психики», «Эксперимент» = "
        "«Практическое наблюдение в жизни»).\n"
        "3. ДИНАМИЧЕСКИЙ СИНТЕЗ ВМЕСТО ШАБЛОНОВ: Не описывай элементы изолированно. "
        "Сшивай формулу: «Если у человека [Открытый центр] + [Определённый мотор] + [Профиль], "
        "то в реальной жизни это приводит к боли [Х]».\n"
        "4. ЗАПРЕТ MARKDOWN-ЗАГОЛОВКОВ В JSON: Внутри текстовых полей JSON строго запрещено "
        "использовать символы #, ##, ###. Для разделения абзацев используй только \\n.\n"
        "5. ТЕМПЕРАТУРА ГЕНЕРАЦИИ: Будь максимально точен, ёмок, избегай «воды» и общих "
        "коучинговых клише («просто верь в себя», «слушай тело»).\n\n"
        "МЕТОДОЛОГИЯ ICF-КОУЧИНГА:\n"
        "- Перейди от директивного тона («Ты должен») к исследовательской позиции.\n"
        "- Подсвечивай соматические маркеры, по которым клиент отлавливает ментальную ловушку.\n"
        "- Эксперименты формулируй по SMART: таймфреймы, микро-действия, критерии успеха.\n\n"
        f"{_GENETIC_SYNTHESIS_FEW_SHOT}\n\n"
        "ВЫДАЧА: строго один JSON-объект без markdown-обёртки ```:\n"
        '{"synthesis_anchor": "...", "client_pain": "...", "false_self_pattern": "...", '
        '"body_signal": "...", "reflective_questions": ["...", "...", "..."], '
        '"experiments": [{"timeframe": "days_1-5", "action": "...", "metric": "...", '
        '"success_criteria": "..."}, {"timeframe": "days_6-15", ...}, '
        '{"timeframe": "days_16-30", ...}]}'
    )

    user_prompt = (
        "Входные данные (Факты из Python-бэкенда — абсолютно точные, не подлежат сомнению):\n"
        f"- Текущая сфера анализа (Domain): {normalized_domain} ({domain_ru})\n"
        f"- Профиль: {profile}\n"
        f"- Внутренний Авторитет: {authority}\n"
        f"- Стратегия Типа: {strategy}\n"
        f"- Тип определенности (Definition): {definition}\n"
        f"- Верифицированные активные каналы: {active_channels_line}\n"
        f"- Текущая синтез-пара: Открытый центр [{open_center}] × "
        f"Определённые моторы/якоря {anchors}\n"
        f"- Серверные шкалы энергии (Read-Only): {scales_line}\n\n"
        "Сгенерируй JSON по схеме из system-инструкции. "
        "Любое отклонение от структуры JSON, использование запрещённых слов "
        "или символов # приведёт к ошибке валидации."
    )
    return system_prompt, user_prompt


def _synthesis_text_has_markdown_headers(text: str) -> bool:
    return bool(re.search(r"^#{1,6}\s", text or "", flags=re.MULTILINE))


def _synthesis_text_banned_hits(text: str) -> list[str]:
    lowered = (text or "").lower()
    return [marker for marker in _GENETIC_SYNTHESIS_BANNED_MARKERS if marker in lowered]


def _normalize_synthesis_experiment(raw: object, timeframe: str) -> dict[str, str]:
    data = raw if isinstance(raw, dict) else {}
    fields = ("action", "metric", "success_criteria")
    normalized: dict[str, str] = {"timeframe": timeframe}
    for field in fields:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"synthesis experiment missing non-empty {field!r}")
        normalized[field] = value.strip()
    return normalized


def _normalize_synthesis_response(parsed: dict[str, object]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for key in _SYNTHESIS_STRING_KEYS:
        value = parsed.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"synthesis JSON missing non-empty {key!r}")
        cleaned = value.strip()
        if _synthesis_text_has_markdown_headers(cleaned):
            raise ValueError(f"synthesis field {key!r} contains markdown headers")
        hits = _synthesis_text_banned_hits(cleaned)
        if hits:
            raise ValueError(f"synthesis field {key!r} contains banned markers: {hits[:3]}")
        report[key] = cleaned

    questions_raw = parsed.get("reflective_questions")
    if not isinstance(questions_raw, list) or len(questions_raw) != 3:
        raise ValueError("synthesis JSON requires exactly 3 reflective_questions")
    questions: list[str] = []
    for idx, item in enumerate(questions_raw):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"synthesis reflective_questions[{idx}] must be non-empty string")
        q = item.strip()
        if _synthesis_text_has_markdown_headers(q):
            raise ValueError(f"synthesis reflective_questions[{idx}] contains markdown headers")
        questions.append(q)
    report["reflective_questions"] = questions

    experiments_raw = parsed.get("experiments")
    if not isinstance(experiments_raw, list) or len(experiments_raw) != 3:
        raise ValueError("synthesis JSON requires exactly 3 experiments")
    experiments: list[dict[str, str]] = []
    for idx, timeframe in enumerate(_SYNTHESIS_EXPERIMENT_TIMEFRAMES):
        exp = _normalize_synthesis_experiment(experiments_raw[idx], timeframe)
        for field in ("action", "metric", "success_criteria"):
            if _synthesis_text_banned_hits(exp[field]):
                raise ValueError(f"synthesis experiment[{idx}] contains banned markers")
        experiments.append(exp)
    report["experiments"] = experiments
    return report


def _parse_synthesis_response_from_llm(raw: str) -> dict[str, Any]:
    parsed = _parse_json_object(raw)
    return _normalize_synthesis_response(parsed)


def render_synthesis_block(synthesis: dict[str, Any]) -> str:
    """Plain-text фрагмент главы из JSON Genetic Synthesis."""
    parts: list[str] = [
        str(synthesis.get("synthesis_anchor") or "").strip(),
        "",
        str(synthesis.get("client_pain") or "").strip(),
        "",
        str(synthesis.get("false_self_pattern") or "").strip(),
        "",
        f"Соматический маркер: {str(synthesis.get('body_signal') or '').strip()}",
        "",
        "Вопросы для исследования:",
    ]
    for question in synthesis.get("reflective_questions") or []:
        parts.append(f"- {question}")
    parts.append("")
    parts.append("Практические наблюдения:")
    for experiment in synthesis.get("experiments") or []:
        if not isinstance(experiment, dict):
            continue
        parts.append(
            f"{experiment.get('timeframe', '')}: {experiment.get('action', '')} | "
            f"Метрика: {experiment.get('metric', '')} | "
            f"Успех: {experiment.get('success_criteria', '')}"
        )
    return strip_hd_markdown_for_plain("\n".join(parts).strip())


async def _generate_synthesis_via_openrouter(
    system_prompt: str,
    user_prompt: str,
    *,
    models: list[str] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    from services.ai_text import ask_ai_messages

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    completion = await ask_ai_messages(
        _app_settings,
        messages,
        timeout=timeout if timeout is not None else _OPENROUTER_PREMIUM_TIMEOUT_SEC,
        models=models or _openrouter_models_for_premium(),
        max_tokens=_GENETIC_SYNTHESIS_MAX_TOKENS,
        temperature=_GENETIC_SYNTHESIS_TEMPERATURE,
        response_format={"type": "json_object"},
    )
    text = (completion.get("content") or "").strip()
    if not text:
        raise RuntimeError("openrouter_synthesis_empty")
    return _parse_synthesis_response_from_llm(text)


async def _generate_synthesis_via_gemini(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    client = _configure_genai()
    errors: list[str] = []
    gen_cfg = {
        "response_mime_type": "application/json",
        "max_output_tokens": _GENETIC_SYNTHESIS_MAX_TOKENS,
        "system_instruction": system_prompt,
        "temperature": _GENETIC_SYNTHESIS_TEMPERATURE,
    }
    for model_name in _GEMINI_PREMIUM_MODEL_CHAIN:
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=gen_cfg,
                ),
                timeout=_GEMINI_PREMIUM_TIMEOUT_SEC,
            )
            report = _parse_synthesis_response_from_llm(_extract_gemini_text(response))
            logger.info("HD genetic synthesis Gemini model=%s", model_name)
            return report
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{model_name}: {exc!r}")
            continue
    raise RuntimeError("gemini_synthesis_unavailable: " + "; ".join(errors))


async def generate_genetic_synthesis(
    *,
    domain: str,
    math_data: dict[str, object],
    synthesis_pair: dict[str, object],
    energy_scales: dict[str, int] | None = None,
) -> dict[str, Any]:
    """
    Один модуль Genetic Synthesis: open_center × anchors × domain → JSON v3.

    LLM вызывается с temperature=0.1; energy_scales — только серверные (read-only).
    """
    scales = _normalize_energy_scales(
        energy_scales if energy_scales is not None else compute_energy_scales_from_math(math_data)
    )
    system_prompt, user_prompt = _build_genetic_synthesis_prompt(
        domain=domain,
        math_data=math_data,
        synthesis_pair=synthesis_pair,
        energy_scales=scales,
    )
    errors: list[str] = []

    if _gemini_configured():
        try:
            return await _generate_synthesis_via_gemini(system_prompt, user_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini genetic synthesis failed, trying OpenRouter: %s", exc)
            errors.append(f"gemini: {exc!r}")
    elif not _openrouter_configured():
        raise RuntimeError("hd_synthesis_unavailable: задайте GEMINI_API_KEY или OPENROUTER_API_KEY")

    try:
        return await _generate_synthesis_via_openrouter(system_prompt, user_prompt)
    except Exception as exc:  # noqa: BLE001
        logger.exception("OpenRouter genetic synthesis failed")
        errors.append(f"openrouter: {exc!r}")

    raise RuntimeError("hd_synthesis_unavailable: " + "; ".join(errors))


def _compose_domain_chapter(
    domain: str,
    *,
    static_context: str,
    synthesis_blocks: list[dict[str, Any]],
) -> str:
    """Склеивает static-контекст и AI synthesis-блоки в одну главу."""
    domain_titles = {
        "money": "Финансовый генетический синтез",
        "love": "Синтез в отношениях",
        "energy": "Энергетический синтез",
    }
    parts: list[str] = []
    static = static_context.strip()
    if static:
        parts.append(f"{domain_titles.get(domain, domain)}\n\nСтатическая база карты:\n{static}")
    for idx, block in enumerate(synthesis_blocks, start=1):
        rendered = render_synthesis_block(block)
        if rendered:
            open_center = ""
            if isinstance(block.get("_pair"), dict):
                open_center = str(block["_pair"].get("open_center") or "").strip()
            header = f"Синтез {idx}"
            if open_center:
                header = f"Синтез {idx}: открытый центр «{open_center}»"
            parts.append(f"{header}\n{rendered}")
    if not parts:
        return f"Раздел {domain}: данных синтеза недостаточно — опирайся на Chart Overview."
    return "\n\n".join(parts).strip()


def _build_premium_summary_prompt(
    user_name: str,
    math_data: dict[str, object],
    *,
    domain_excerpts: dict[str, str],
    energy_scales: dict[str, int],
) -> tuple[str, str]:
    """Промпт fast_facts + plan на основе готовых глав (последний pass)."""
    name = (user_name or "").strip() or "друг"
    hd_type = str(math_data.get("hd_type") or "")
    profile = str(math_data.get("profile") or "")
    authority = str(math_data.get("authority") or "")
    strategy = str(math_data.get("strategy") or "")
    scales_line = _format_energy_scales_line(energy_scales)

    excerpt_parts: list[str] = []
    for domain in ("money", "love", "energy"):
        text = str(domain_excerpts.get(domain) or "").strip()
        if text:
            excerpt_parts.append(f"[{domain}]\n{text[:2500]}")
    excerpts = "\n\n".join(excerpt_parts) or "Главы синтеза не переданы."

    system_prompt = (
        "Ты — ICF-коуч NeuroMule HD. На основе готовых глав Genetic Synthesis сформируй JSON:\n"
        '{"fast_facts": "...", "plan": "..."}\n'
        f"- fast_facts: до {_FAST_FACTS_MAX_LEN} символов, три строки в одном поле: "
        "'⚡ Главный баг прошивки: …', '💼 Триггер больших денег: …', '🔋 Идеальная перезагрузка: …'.\n"
        "- plan: plain text план на 30 дней (блоки 1–5 / 6–15 / 16–30) с SMART-действиями.\n"
        "Без символов # в тексте. Без эзотерического жаргона. Только факты карты из user-блока."
    )
    user_prompt = (
        f"Клиент: {name}. Тип: {hd_type}. Профиль: {profile}. "
        f"Авторитет: {authority}. Стратегия: {strategy}.\n"
        f"Шкалы (read-only): {scales_line}\n\n"
        f"Выдержки из глав синтеза:\n{excerpts}\n\n"
        "Сгенерируй fast_facts и plan, согласованные с выдержками."
    )
    return system_prompt, user_prompt


def _normalize_premium_summary(parsed: dict[str, object]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("fast_facts", "plan"):
        value = parsed.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"premium summary missing non-empty {key!r}")
        out[key] = value.strip()
    if len(out["fast_facts"]) > _FAST_FACTS_MAX_LEN:
        out["fast_facts"] = out["fast_facts"][: _FAST_FACTS_MAX_LEN - 1].rstrip() + "…"
    return out


async def _generate_premium_summary_via_openrouter(
    system_prompt: str,
    user_prompt: str,
    *,
    models: list[str] | None = None,
    timeout: float | None = None,
) -> dict[str, str]:
    from services.ai_text import ask_ai_messages

    completion = await ask_ai_messages(
        _app_settings,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        timeout=timeout if timeout is not None else _OPENROUTER_PREMIUM_TIMEOUT_SEC,
        models=models or _openrouter_models_for_premium(),
        max_tokens=_PREMIUM_SUMMARY_MAX_TOKENS,
        temperature=_PREMIUM_SUMMARY_TEMPERATURE,
        response_format={"type": "json_object"},
    )
    text = (completion.get("content") or "").strip()
    if not text:
        raise RuntimeError("openrouter_premium_summary_empty")
    return _normalize_premium_summary(_parse_json_object(text))


async def _generate_premium_summary_via_gemini(
    system_prompt: str,
    user_prompt: str,
) -> dict[str, str]:
    client = _configure_genai()
    gen_cfg = {
        "response_mime_type": "application/json",
        "max_output_tokens": _PREMIUM_SUMMARY_MAX_TOKENS,
        "system_instruction": system_prompt,
        "temperature": _PREMIUM_SUMMARY_TEMPERATURE,
    }
    for model_name in _GEMINI_PREMIUM_MODEL_CHAIN:
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=gen_cfg,
                ),
                timeout=_GEMINI_PREMIUM_TIMEOUT_SEC,
            )
            return _normalize_premium_summary(_parse_json_object(_extract_gemini_text(response)))
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("gemini_premium_summary_unavailable")


async def _generate_premium_summary(
    user_name: str,
    math_data: dict[str, object],
    *,
    domain_excerpts: dict[str, str],
    energy_scales: dict[str, int],
    upgrade_mode: bool = False,
) -> dict[str, str]:
    system_prompt, user_prompt = _build_premium_summary_prompt(
        user_name,
        math_data,
        domain_excerpts=domain_excerpts,
        energy_scales=energy_scales,
    )
    if _gemini_configured() and not upgrade_mode:
        try:
            return await _generate_premium_summary_via_gemini(system_prompt, user_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini premium summary failed: %s", exc)
    if not _openrouter_configured():
        raise RuntimeError("hd_summary_unavailable")
    models = (
        _openrouter_models_for_premium_upgrade()
        if upgrade_mode
        else _openrouter_models_for_premium()
    )
    timeout = (
        _OPENROUTER_PREMIUM_UPGRADE_TIMEOUT_SEC
        if upgrade_mode
        else _OPENROUTER_PREMIUM_TIMEOUT_SEC
    )
    return await _generate_premium_summary_via_openrouter(
        system_prompt,
        user_prompt,
        models=models,
        timeout=timeout,
    )


async def _generate_premium_report_multipass(
    user_name: str,
    math_data: dict[str, object],
    *,
    upgrade_mode: bool = False,
) -> dict[str, Any]:
    from services.hd_static_blocks import (
        assemble_static_reference,
        format_static_reference_for_domain,
        format_static_reference_full,
    )

    energy_scales = compute_energy_scales_from_math(math_data)
    static_sections = assemble_static_reference(math_data, gate_to_center=_GATE_TO_CENTER)
    static_full = format_static_reference_full(static_sections)

    pairs_raw = list(math_data.get("synthesis_pairs") or [])
    if not pairs_raw:
        pairs_raw = build_synthesis_pairs(math_data)
    max_pairs = _MAX_SYNTHESIS_PAIRS_UPGRADE if upgrade_mode else _MAX_SYNTHESIS_PAIRS_FULL
    pairs = pairs_raw[:max_pairs]

    synthesis_by_domain: dict[str, list[dict[str, Any]]] = {
        "money": [],
        "love": [],
        "energy": [],
    }
    failed_pairs = 0

    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        for domain in sorted(_GENETIC_SYNTHESIS_DOMAINS):
            try:
                block = await generate_genetic_synthesis(
                    domain=domain,
                    math_data=math_data,
                    synthesis_pair=pair,
                    energy_scales=energy_scales,
                )
                block["_pair"] = {
                    "open_center": pair.get("open_center"),
                    "anchors": pair.get("anchors"),
                }
                synthesis_by_domain[domain].append(block)
            except Exception:
                failed_pairs += 1
                logger.warning(
                    "genetic synthesis failed domain=%s open=%s",
                    domain,
                    pair.get("open_center"),
                    exc_info=True,
                )

    successful = sum(len(v) for v in synthesis_by_domain.values())
    if successful == 0:
        raise RuntimeError("multipass_synthesis_empty")

    domain_chapters: dict[str, str] = {}
    domain_excerpts: dict[str, str] = {}
    for domain in ("money", "love", "energy"):
        static_ctx = format_static_reference_for_domain(static_sections, domain)
        chapter = _compose_domain_chapter(
            domain,
            static_context=static_ctx,
            synthesis_blocks=synthesis_by_domain[domain],
        )
        domain_chapters[domain] = chapter
        domain_excerpts[domain] = chapter[:4000]

    summary = await _generate_premium_summary(
        user_name,
        math_data,
        domain_excerpts=domain_excerpts,
        energy_scales=energy_scales,
        upgrade_mode=upgrade_mode,
    )

    return {
        "fast_facts": summary["fast_facts"],
        "money": domain_chapters["money"],
        "love": domain_chapters["love"],
        "energy": domain_chapters["energy"],
        "plan": summary["plan"],
        "energy_scales": energy_scales,
        "static_reference": static_sections,
        "synthesis_meta": {
            "pairs_requested": len(pairs),
            "blocks_ok": successful,
            "blocks_failed": failed_pairs,
            "static_pages_est": max(1, len(static_full) // 2200),
        },
    }


def _pdf_clean_meta_value(value: object) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if not text or lowered in {
        "не указан",
        "не указаны",
        "не передан",
        "не передана",
        "—",
        "-",
        "unknown",
    }:
        return ""
    return text


def _md_to_reportlab_html(text: object) -> str:
    """Конвертирует ограниченный markdown (**жирный**, переносы) в HTML для Paragraph."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    chunks: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            chunks.append("<br/>")
            continue
        escaped = html_module.escape(stripped)
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
        if stripped.startswith("### "):
            escaped = f"<b><font size='12'>{html_module.escape(stripped[4:])}</font></b>"
        elif stripped.startswith("## "):
            escaped = f"<b><font size='13'>{html_module.escape(stripped[3:])}</font></b>"
        chunks.append(escaped)
    return "<br/>".join(chunks)


_PdfFlowableBase = Flowable if Flowable is not None else object


class _HdPdfBookmark(_PdfFlowableBase):
    """Нулевой flowable: регистрирует интерактивную закладку PDF."""

    height = width = 0

    def __init__(self, title: str, key: str) -> None:
        super().__init__()
        self.title = title
        self.key = key

    def draw(self) -> None:
        self.canv.bookmarkPage(self.key)
        self.canv.addOutlineEntry(self.title, self.key, level=0)


class _HdAccentBarFlowable(_PdfFlowableBase):
    """Горизонтальная фиолетовая полоса под заголовком главы."""

    def __init__(self, width: float = 480, *, bar_height: float = 4) -> None:
        super().__init__()
        self.width = width
        self.bar_height = bar_height
        self.height = bar_height + 10

    def draw(self) -> None:
        if colors is None:
            return
        self.canv.setFillColor(colors.HexColor(_HD_NEON_HEX))
        self.canv.roundRect(0, 4, self.width, self.bar_height, 2, fill=1, stroke=0)


class _HdEnergyScalesFlowable(_PdfFlowableBase):
    """Три progress bar по energy_scales (capacity, immunity, scale)."""

    _LABELS: tuple[tuple[str, str], ...] = (
        ("capacity", "Ёмкость ауры"),
        ("immunity", "Иммунитет к чужому мнению"),
        ("scale", "Индекс харизмы"),
    )

    def __init__(
        self,
        scales: dict[str, object],
        *,
        width: float = 460,
        font_name: str = "Helvetica",
    ) -> None:
        super().__init__()
        self.scales = scales if isinstance(scales, dict) else {}
        self.width = width
        self.font_name = font_name
        self.bar_h = 12.0
        self.row_gap = 30.0
        self.height = self.row_gap * len(self._LABELS) + 16

    def draw(self) -> None:
        if colors is None:
            return
        bar_max_w = self.width - 130
        y = self.height - 18
        for key, label in self._LABELS:
            pct = _clamp_scale(self.scales.get(key), default=50)
            fill_w = max(2.0, bar_max_w * pct / 100.0)
            self.canv.setFont(self.font_name, 9)
            self.canv.setFillColor(colors.HexColor("#555566"))
            self.canv.drawString(0, y + 2, label)
            bx = 130
            self.canv.setFillColor(colors.HexColor("#D8D8E0"))
            self.canv.roundRect(bx, y, bar_max_w, self.bar_h, 3, fill=1, stroke=0)
            self.canv.setFillColor(colors.HexColor(_HD_NEON_HEX))
            self.canv.roundRect(bx, y, fill_w, self.bar_h, 3, fill=1, stroke=0)
            self.canv.setFillColor(colors.HexColor("#1A1A24"))
            self.canv.drawRightString(bx + bar_max_w + 36, y + 2, f"{pct}%")
            y -= self.row_gap


class _HdPremiumPdfDoc(BaseDocTemplate):
    """Platypus-документ с тёмной обложкой, светлыми главами и сквозным футером."""

    def __init__(
        self,
        filename: str,
        *,
        user_name: str,
        birth_data: str,
        font_name: str,
    ) -> None:
        if BaseDocTemplate is None or Frame is None or PageTemplate is None or A4 is None:
            raise RuntimeError("Установите пакет reportlab для PDF-отчетов.")
        self.hd_user_name = (user_name or "").strip() or "друг"
        self.hd_birth_data = (birth_data or "").strip()
        self.hd_font_name = font_name
        super().__init__(
            filename,
            pagesize=A4,
            leftMargin=48,
            rightMargin=48,
            topMargin=56,
            bottomMargin=56,
        )
        frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="hd_main",
        )
        self.addPageTemplates(
            [
                PageTemplate(id="Cover", frames=[frame], onPage=self._on_cover_page),
                PageTemplate(id="Content", frames=[frame], onPage=self._on_content_page),
            ]
        )

    def _on_cover_page(self, canv: Any, doc: Any) -> None:
        if colors is None or A4 is None:
            return
        w, h = A4
        canv.saveState()
        canv.setFillColor(colors.HexColor(_PDF_COVER_BG))
        canv.rect(0, 0, w, h, fill=1, stroke=0)
        name = self.hd_user_name.strip() or "друг"
        title = f"{name.upper()}. ПЕРСОНАЛЬНЫЙ НАВИГАТОР ЛИЧНОСТИ"
        canv.setFillColor(colors.HexColor(_HD_NEON_HEX))
        canv.setFont(self.hd_font_name, 20)
        canv.drawCentredString(w / 2, h * 0.58, title[:72])
        canv.setFillColor(colors.HexColor("#C8C8D8"))
        canv.setFont(self.hd_font_name, 13)
        canv.drawCentredString(w / 2, h * 0.50, "Квантовый аудит энергетической архитектуры")
        if self.hd_birth_data:
            canv.setFont(self.hd_font_name, 11)
            canv.drawCentredString(w / 2, h * 0.44, self.hd_birth_data[:90])
        canv.setFillColor(colors.HexColor("#888899"))
        canv.setFont(self.hd_font_name, 9)
        canv.drawCentredString(w / 2, 72, _HD_WATERMARK)
        canv.restoreState()

    def _on_content_page(self, canv: Any, doc: Any) -> None:
        if colors is None or A4 is None:
            return
        w, _h = A4
        canv.saveState()
        canv.setFillColor(colors.HexColor(_PDF_CONTENT_BG))
        canv.rect(0, 0, w, A4[1], fill=1, stroke=0)
        canv.setFont(self.hd_font_name, 8)
        canv.setFillColor(colors.HexColor("#888899"))
        canv.drawString(48, 24, _HD_WATERMARK)
        canv.drawRightString(w - 48, 24, str(canv.getPageNumber()))
        canv.restoreState()


def _prepare_bodygraph_for_pdf(user_id: int, birth_data: str | None) -> str | None:
    """JPEG ~430 px, ≤300 KB для быстрой загрузки в Telegram."""
    if Image is None:
        return None
    try:
        defined, _ = _defined_centers_from_birth_data(birth_data or "")
        generate_premium_bodygraph(sorted(defined), user_id)
        src = _HD_BODYGRAPH_OUTPUT_DIR / f"ready_hd_{user_id}.png"
        if not src.is_file():
            return None
        with Image.open(src) as img:
            ratio = _PDF_BODYGRAPH_WIDTH_PX / max(img.width, 1)
            new_size = (_PDF_BODYGRAPH_WIDTH_PX, max(1, int(img.height * ratio)))
            resized = img.convert("RGB").resize(new_size, Image.Resampling.LANCZOS)
        out = Path(tempfile.gettempdir()) / f"hd_bg_pdf_{user_id}.jpg"
        for quality in (88, 82, 75, 68, 60):
            resized.save(out, format="JPEG", quality=quality, optimize=True)
            if out.stat().st_size <= _PDF_BODYGRAPH_MAX_BYTES:
                break
        return str(out)
    except Exception:
        logger.warning("bodygraph pdf optimize failed uid=%s", user_id, exc_info=True)
        return None


def _build_chart_overview_table(
    meta: dict[str, object],
    font_name: str,
) -> Table:
    rows: list[list[str]] = []
    for label, raw in (
        ("Истинный Тип", meta.get("hd_type")),
        ("Профиль", meta.get("profile")),
        ("Внутренний Авторитет", meta.get("authority")),
        ("Стратегия", meta.get("strategy")),
        ("Определённость", meta.get("definition")),
    ):
        value = _pdf_clean_meta_value(raw)
        if value:
            rows.append([label, value])
    defined = meta.get("defined_centers")
    if isinstance(defined, list) and defined:
        rows.append(["Определённые центры", ", ".join(str(c) for c in defined[:9])])
    if not rows:
        rows.append(["Карта", "Параметры рассчитаны по дате рождения"])
    table = Table(rows, colWidths=[150, 330])
    if colors is not None and TableStyle is not None:
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDE9FE")),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#D4D4DC")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E8E8EE")),
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("FONTNAME", (0, 0), (0, -1), font_name),
                    ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555566")),
                    ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#1A1A24")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
    return table


def _build_hd_premium_pdf_story(
    user_id: int,
    report: dict[str, Any],
    *,
    user_name: str,
    birth_data: str | None,
    meta: dict[str, object],
    font_name: str,
) -> list[Any]:
    if (
        Paragraph is None
        or PageBreak is None
        or Spacer is None
        or NextPageTemplate is None
        or ParagraphStyle is None
        or RLImage is None
    ):
        raise RuntimeError("Установите пакет reportlab для PDF-отчетов.")

    title_style = ParagraphStyle(
        "HdChapterTitle",
        fontName=font_name,
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#1A1A24") if colors else None,
        spaceAfter=4,
    )
    overview_style = ParagraphStyle(
        "HdOverviewTitle",
        fontName=font_name,
        fontSize=18,
        leading=22,
        textColor=colors.HexColor(_HD_NEON_HEX) if colors else None,
        spaceAfter=10,
    )
    body_style = ParagraphStyle(
        "HdBody",
        fontName=font_name,
        fontSize=11,
        leading=15,
        textColor=colors.HexColor("#1A1A24") if colors else None,
        spaceAfter=8,
    )

    story: list[Any] = [Spacer(1, 1)]
    story.append(NextPageTemplate("Content"))
    story.append(PageBreak())

    story.append(_HdPdfBookmark("Chart Overview", "hd_overview"))
    story.append(Paragraph("Chart Overview", overview_style))
    story.append(Spacer(1, 8))
    story.append(_build_chart_overview_table(meta, font_name))
    story.append(Spacer(1, 16))

    bg_path = _prepare_bodygraph_for_pdf(user_id, birth_data)
    if bg_path:
        display_w = _PDF_BODYGRAPH_WIDTH_PX * 0.72
        img = RLImage(bg_path, width=display_w, height=display_w, kind="proportional")
        img.hAlign = "CENTER"
        story.append(img)
        story.append(Spacer(1, 12))

    scales = report.get("energy_scales")
    if isinstance(scales, dict):
        story.append(Paragraph("<b>Energy Scales</b>", body_style))
        story.append(_HdEnergyScalesFlowable(scales, font_name=font_name))
    story.append(PageBreak())

    chapter_blocks: list[tuple[str, str, str, str]] = []
    for key, chapter_title, bookmark_key in _PDF_CHAPTER_SPECS:
        body = report.get(key)
        if body:
            chapter_blocks.append((key, chapter_title, bookmark_key, str(body)))

    for idx, (_key, chapter_title, bookmark_key, body) in enumerate(chapter_blocks):
        story.append(_HdPdfBookmark(chapter_title, bookmark_key))
        story.append(Paragraph(html_module.escape(chapter_title), title_style))
        story.append(_HdAccentBarFlowable(width=480))
        story.append(Spacer(1, 10))
        html_body = _md_to_reportlab_html(body)
        if html_body:
            story.append(Paragraph(html_body, body_style))
        if idx < len(chapter_blocks) - 1:
            story.append(PageBreak())

    return story


def create_hd_premium_pdf(
    user_id: int,
    report: dict[str, Any],
    birth_data: str | None,
    *,
    hd_type: str = "",
    user_name: str = "",
) -> str:
    """Премиальный PDF: обложка, Chart Overview, energy scales, главы с закладками."""
    if BaseDocTemplate is None or A4 is None:
        raise RuntimeError("Установите пакет reportlab для PDF-отчетов.")
    math_data = build_hd_math_data(hd_type or "не указан", birth_data or "")
    meta = hd_profile_metadata(math_data)
    report_for_pdf: dict[str, Any] = dict(report)
    static_raw = report_for_pdf.get("static_reference")
    if isinstance(static_raw, dict) and static_raw:
        from services.hd_static_blocks import format_static_reference_full

        report_for_pdf["static_reference"] = format_static_reference_full(static_raw)
    elif not str(report_for_pdf.get("static_reference") or "").strip():
        from services.hd_static_blocks import assemble_static_reference, format_static_reference_full

        sections = assemble_static_reference(math_data, gate_to_center=_GATE_TO_CENTER)
        report_for_pdf["static_reference"] = format_static_reference_full(sections)
    path = Path(tempfile.gettempdir()) / f"report_{user_id}.pdf"
    font_name = _register_pdf_font()
    doc = _HdPremiumPdfDoc(
        str(path),
        user_name=user_name,
        birth_data=str(meta.get("birth_data") or birth_data or ""),
        font_name=font_name,
    )
    story = _build_hd_premium_pdf_story(
        user_id,
        report_for_pdf,
        user_name=user_name,
        birth_data=birth_data,
        meta=meta,
        font_name=font_name,
    )
    doc.build(story)
    return str(path)


def create_pdf(
    user_id: int,
    text: str,
    birth_data: str | None = None,
    *,
    hd_type: str = "",
    profile: str = "",
    authority: str = "",
    strategy: str = "",
    user_name: str = "",
) -> str:
    """Обратная совместимость: собирает report-dict из plain text и вызывает premium PDF."""
    _ = (text, profile, authority, strategy)
    report = {
        "money": text,
        "love": "",
        "energy": "",
        "plan": "",
        "energy_scales": {"capacity": 50, "immunity": 50, "scale": 50},
    }
    return create_hd_premium_pdf(
        user_id,
        report,
        birth_data,
        hd_type=hd_type,
        user_name=user_name,
    )


_HD_BIRTH_NOISE_RE: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bгород\b", re.IGNORECASE),
    re.compile(r"\bг\.\s*", re.IGNORECASE),
    re.compile(r"\bг\s+(?=[^\d])", re.IGNORECASE),
    re.compile(r"\s+в\s+", re.IGNORECASE),
)

_HD_TYPE_PREFIX_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?:манифест(?:ирующий)?\s+генератор|м\.?\s*г\.?|мг)\b", re.I), "Манифестирующий Генератор"),
    (re.compile(r"^manifesting\s+generator\b", re.I), "Манифестирующий Генератор"),
    (re.compile(r"^манифестор\b", re.I), "Манифестор"),
    (re.compile(r"^manifestor\b", re.I), "Манифестор"),
    (re.compile(r"^генератор\b", re.I), "Генератор"),
    (re.compile(r"^generator\b", re.I), "Генератор"),
    (re.compile(r"^проектор\b", re.I), "Проектор"),
    (re.compile(r"^projector\b", re.I), "Проектор"),
    (re.compile(r"^рефлектор\b", re.I), "Рефлектор"),
    (re.compile(r"^reflector\b", re.I), "Рефлектор"),
)


def _normalize_hd_birth_string(text: str) -> str:
    """Запятые → пробелы, шумовые слова, схлопывание пробелов."""
    normalized = (text or "").replace(",", " ")
    for pattern in _HD_BIRTH_NOISE_RE:
        normalized = pattern.sub(" ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _extract_leading_hd_type(text: str) -> tuple[str, str]:
    """Вырезает тип HD из начала строки; остаток — дата/время/город."""
    cleaned = (text or "").strip()
    if not cleaned:
        return "не указан", ""
    for pattern, label in _HD_TYPE_PREFIX_RULES:
        match = pattern.match(cleaned)
        if match:
            rest = cleaned[match.end() :].lstrip(" ,.:;-")
            return label, rest
    return "не указан", cleaned


def parse_hd_request(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    if not text:
        return "не указан", ""

    hd_type = "не указан"
    birth_chunks: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("тип:"):
            type_val = stripped.split(":", 1)[1].strip()
            norm_type_val = _normalize_hd_birth_string(type_val)
            extracted, rest = _extract_leading_hd_type(norm_type_val)
            if extracted != "не указан":
                hd_type = extracted
                if rest:
                    birth_chunks.append(rest)
            elif type_val:
                hd_type = type_val
        else:
            birth_chunks.append(stripped)

    body = " ".join(birth_chunks).strip() or text
    normalized = _normalize_hd_birth_string(body)

    if hd_type == "не указан" and normalized:
        hd_type, normalized = _extract_leading_hd_type(normalized)

    birth_data = normalized or body
    return hd_type, birth_data


_STORY_CANVAS_SIZE = (1080, 1920)


def _load_story_font(size: int) -> Any:
    if ImageFont is None:
        return None
    for name in ("Montserrat-Bold.ttf", "Montserrat.ttf", "Inter-Bold.ttf", "Inter.ttf", "arial.ttf"):
        for folder in (_PROJECT_ROOT / "assets", Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"):
            candidate = folder / name
            if candidate.is_file():
                try:
                    return ImageFont.truetype(str(candidate), size=size)
                except OSError:
                    continue
    return ImageFont.load_default()


def _draw_story_watermark(draw: Any, width: int, height: int, font: Any) -> None:
    if ImageDraw is None:
        return
    label = _HD_WATERMARK_PLAIN
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((width - tw) // 2, height - 80), label, fill=(200, 200, 210, 220), font=font)


def _create_story_gradient(size: tuple[int, int]) -> Any:
    """Вертикальный неоновый градиент для Stories (фиолетовый → тёмно-синий)."""
    if Image is None or ImageDraw is None:
        raise RuntimeError("Pillow required")
    w, h = size
    base = Image.new("RGBA", size, (12, 8, 32, 255))
    draw = ImageDraw.Draw(base)
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(72 * (1 - t) + 10 * t)
        g = int(18 * (1 - t) + 14 * t)
        b = int(110 * (1 - t) + 36 * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b, 255))
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((w // 2 - 420, h // 3 - 280, w // 2 + 420, h // 3 + 280), fill=(139, 92, 246, 48))
    glow_draw.ellipse((w // 4 - 200, h * 2 // 3 - 160, w // 4 + 200, h * 2 // 3 + 160), fill=(56, 189, 248, 32))
    return Image.alpha_composite(base, glow)


def _story_excerpt(text: object, *, max_chars: int = 380) -> str:
    clean = strip_hd_markdown_for_plain(str(text or "").strip())
    if not clean:
        return ""
    if len(clean) <= max_chars:
        return clean
    cut = clean[:max_chars].rsplit(" ", 1)[0]
    return f"{cut}…"


def _draw_story_meta_panel(
    draw: Any,
    math_data: dict[str, object],
    *,
    y_top: int,
    label_font: Any,
    value_font: Any,
) -> None:
    meta = hd_profile_metadata(math_data)
    rows = (
        ("Тип", str(meta.get("hd_type") or "—")),
        ("Профиль", str(meta.get("profile") or "—")),
        ("Авторитет", str(meta.get("authority") or "—")),
        ("Стратегия", str(meta.get("strategy") or "—")),
    )
    panel_x, panel_w = 48, 984
    row_h = 52
    panel_h = 36 + row_h * len(rows)
    y0 = y_top
    draw.rounded_rectangle((panel_x, y0, panel_x + panel_w, y0 + panel_h), radius=28, fill=(8, 8, 18, 210))
    draw.text((panel_x + 24, y0 + 16), "Параметры карты", fill=_HD_NEON_HEX, font=label_font)
    cy = y0 + 48
    for label, value in rows:
        draw.text((panel_x + 24, cy), label, fill=(180, 180, 200, 255), font=value_font)
        wrapped = textwrap.wrap(value, width=34) or [value]
        draw.text((panel_x + 200, cy), wrapped[0], fill=(245, 245, 252, 255), font=value_font)
        cy += row_h


def generate_instagram_stories(
    uid: int,
    report: dict[str, Any],
    *,
    math_data: dict[str, object] | None = None,
    hd_type: str = "",
    birth_data: str = "",
) -> list[str]:
    """
    Instagram Stories: бодиграф + параметры карты; подробные выдержки из разделов отчёта.

    Returns:
        ``tmp/story_{uid}_1.png``, ``tmp/story_{uid}_2.png``.
    """
    if Image is None or ImageDraw is None or ImageFilter is None:
        raise RuntimeError("Установите пакет Pillow для Instagram Stories.")

    if math_data is None:
        math_data = build_hd_math_data(hd_type or "не указан", birth_data or "")

    os.makedirs(str(_HD_BODYGRAPH_OUTPUT_DIR), exist_ok=True)
    bodygraph_path = _HD_BODYGRAPH_OUTPUT_DIR / f"ready_hd_{uid}.png"
    paths: list[str] = []
    title_font = _load_story_font(44)
    subtitle_font = _load_story_font(24)
    label_font = _load_story_font(28)
    value_font = _load_story_font(24)
    section_font = _load_story_font(30)
    body_font = _load_story_font(24)
    meta = hd_profile_metadata(math_data)
    display_type = str(meta.get("hd_type") or hd_type or "Human Design")

    # --- Карточка 1: градиент + бодиграф + параметры ---
    card1 = _create_story_gradient(_STORY_CANVAS_SIZE)
    if bodygraph_path.is_file():
        bg_src = Image.open(bodygraph_path).convert("RGBA")
        graph_w = min(760, _STORY_CANVAS_SIZE[0] - 160)
        graph_h = int(graph_w * bg_src.height / max(bg_src.width, 1))
        graph = bg_src.resize((graph_w, graph_h), Image.Resampling.LANCZOS)
        glow_layer = graph.copy()
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=18))
        gx = (_STORY_CANVAS_SIZE[0] - graph_w) // 2
        gy = 280
        card1.paste(glow_layer, (gx - 8, gy - 8), glow_layer)
        card1.paste(graph, (gx, gy), graph)

    draw1 = ImageDraw.Draw(card1)
    draw1.text((60, 100), "Human Design Premium", fill=_HD_NEON_HEX, font=title_font)
    birth_line = strip_hd_markdown_for_plain(str(meta.get("birth_data") or birth_data or "").strip())
    if birth_line:
        draw1.text((60, 158), birth_line[:48], fill=(210, 210, 225, 230), font=subtitle_font)
    draw1.text((60, 200), display_type, fill=(255, 255, 255, 255), font=label_font)
    _draw_story_meta_panel(
        draw1,
        math_data,
        y_top=1180,
        label_font=label_font,
        value_font=value_font,
    )
    _draw_story_watermark(draw1, _STORY_CANVAS_SIZE[0], _STORY_CANVAS_SIZE[1], _load_story_font(22))
    out1 = _HD_BODYGRAPH_OUTPUT_DIR / f"story_{uid}_1.png"
    card1.convert("RGB").save(out1, format="PNG")
    paths.append(f"tmp/story_{uid}_1.png")

    # --- Карточка 2: подробные выдержки money / love / energy ---
    card2 = _create_story_gradient(_STORY_CANVAS_SIZE)
    draw2 = ImageDraw.Draw(card2)
    draw2.text((60, 100), display_type, fill=_HD_NEON_HEX, font=title_font)
    draw2.text((60, 158), "Ключевые инсайты разбора", fill=(210, 210, 225, 230), font=subtitle_font)

    sections = (
        ("💼 Деньги и ресурс", _story_excerpt(report.get("money"), max_chars=420)),
        ("❤️ Отношения", _story_excerpt(report.get("love"), max_chars=420)),
        ("⚡ Энергия и режим", _story_excerpt(report.get("energy"), max_chars=360)),
    )
    y_pos = 240
    for title, body in sections:
        if not body:
            continue
        lines = textwrap.wrap(body, width=38) or [body]
        box_h = 56 + len(lines) * 32
        if y_pos + box_h > _STORY_CANVAS_SIZE[1] - 120:
            break
        draw2.rounded_rectangle((48, y_pos, 1032, y_pos + box_h), radius=24, fill=(8, 8, 18, 215))
        draw2.text((72, y_pos + 16), title, fill=_HD_NEON_HEX, font=section_font)
        ty = y_pos + 56
        for line in lines:
            draw2.text((72, ty), line, fill=(235, 235, 245, 255), font=body_font)
            ty += 32
        y_pos += box_h + 20

    if y_pos < 900:
        fast = _story_excerpt(report.get("fast_facts"), max_chars=280)
        if fast:
            lines = textwrap.wrap(fast, width=38)
            box_h = 56 + len(lines) * 32
            draw2.rounded_rectangle((48, y_pos, 1032, y_pos + box_h), radius=24, fill=(8, 8, 18, 215))
            draw2.text((72, y_pos + 16), "⚡ Экспресс-вывод", fill=_HD_NEON_HEX, font=section_font)
            ty = y_pos + 56
            for line in lines:
                draw2.text((72, ty), line, fill=(235, 235, 245, 255), font=body_font)
                ty += 32

    _draw_story_watermark(draw2, _STORY_CANVAS_SIZE[0], _STORY_CANVAS_SIZE[1], _load_story_font(22))
    out2 = _HD_BODYGRAPH_OUTPUT_DIR / f"story_{uid}_2.png"
    card2.convert("RGB").save(out2, format="PNG")
    paths.append(f"tmp/story_{uid}_2.png")
    return paths


async def generate_instagram_stories_async(
    uid: int,
    report: dict[str, Any],
    *,
    math_data: dict[str, object] | None = None,
    hd_type: str = "",
    birth_data: str = "",
) -> list[str]:
    """Pillow offloaded в executor — не блокирует event loop на VDS."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: generate_instagram_stories(
            uid,
            report,
            math_data=math_data,
            hd_type=hd_type,
            birth_data=birth_data,
        ),
    )


def _build_compatibility_prompt(
    user_name: str,
    partner_name: str,
    user_math: dict[str, object],
    partner_math: dict[str, object],
    composite: dict[str, object],
) -> tuple[str, str]:
    system_prompt = (
        "Ты — ICF-коуч и эксперт Human Design для NeuroMule. Анализируешь композит пары "
        "строго по серверным данным Swiss Ephemeris. Без эзотерики, кармы и «вибраций». "
        f"{_ELITE_HD_SERVER_MATH_MANDATE}\n"
        "Ответ — ТОЛЬКО JSON с ключами: attraction, conflicts, growth."
    )
    user_prompt = (
        f"Пара: {user_name} + {partner_name}.\n\n"
        f"Карта {user_name}: тип {user_math.get('hd_type')}, центры "
        f"{user_math.get('defined_centers')}, рождение {user_math.get('birth_data')}.\n"
        f"Карта {partner_name}: тип {partner_math.get('hd_type')}, центры "
        f"{partner_math.get('defined_centers')}, рождение {partner_math.get('birth_data')}.\n"
        f"Композит (расчёт сервера): {json.dumps(composite, ensure_ascii=False)[:4000]}\n\n"
        "JSON:\n"
        "- attraction: магнетизм пары по именам, 800–1500 символов Markdown;\n"
        "- conflicts: зоны бытового трения, 800–1500 символов Markdown;\n"
        "- growth: формула синергии + 3 правила коучинга пары, 800–1500 символов Markdown."
    )
    return system_prompt, user_prompt


async def _generate_compat_via_gemini(system_prompt: str, user_prompt: str) -> dict[str, str]:
    client = _configure_genai()
    gen_cfg = {
        "response_mime_type": "application/json",
        "max_output_tokens": _HD_PREMIUM_MAX_OUTPUT_TOKENS,
        "system_instruction": system_prompt,
    }
    errors: list[str] = []
    for model_name in _GEMINI_PREMIUM_MODEL_CHAIN:
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=gen_cfg,
                ),
                timeout=_GEMINI_PREMIUM_TIMEOUT_SEC,
            )
            report = _parse_compat_report_from_llm(_extract_gemini_text(response))
            logger.info("HD compatibility Gemini model=%s", model_name)
            return report
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{model_name}: {exc!r}")
            continue
    raise RuntimeError("gemini_compat_unavailable: " + "; ".join(errors))


async def _generate_compat_via_openrouter(system_prompt: str, user_prompt: str) -> dict[str, str]:
    from services.ai_text import ask_ai_messages

    completion = await ask_ai_messages(
        _app_settings,
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        timeout=_OPENROUTER_PREMIUM_TIMEOUT_SEC,
        models=_openrouter_models_for_premium(),
        max_tokens=_HD_PREMIUM_MAX_OUTPUT_TOKENS,
        temperature=0.7,
        response_format={"type": "json_object"},
    )
    text = (completion.get("content") or "").strip()
    if not text:
        raise RuntimeError("openrouter_compat_empty")
    return _parse_compat_report_from_llm(text)


async def generate_compatibility_report(
    user_id: int,
    partner_raw: str,
    *,
    user_name: str = "ты",
    partner_name: str = "партнёр",
) -> dict[str, str]:
    """Композит отношений: pyswisseph + тяжёлая LLM (Gemini Pro → Claude 3.5 Sonnet)."""
    user = await get_user(user_id)
    own_type = (user["hd_type"] or "не указан") if "hd_type" in user.keys() else "не указан"
    own_birth = (user["hd_birth_data"] or "").strip() if "hd_birth_data" in user.keys() else ""
    if not own_birth:
        raise ValueError("own_birth_missing")
    partner_type, partner_birth = parse_hd_request(partner_raw)
    if not partner_birth:
        raise ValueError("partner_birth_missing")
    user_math = build_hd_math_data(own_type, own_birth)
    partner_math = build_hd_math_data(partner_type, partner_birth)
    composite = calculate_composite(own_birth, partner_birth)
    system_prompt, user_prompt = _build_compatibility_prompt(
        user_name,
        partner_name,
        user_math,
        partner_math,
        composite,
    )
    if _gemini_configured():
        try:
            report = await _generate_compat_via_gemini(system_prompt, user_prompt)
            return report
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini compatibility failed, OpenRouter fallback: %s", exc)
    elif not _openrouter_configured():
        raise RuntimeError("hd_compat_unavailable: задайте GEMINI_API_KEY или OPENROUTER_API_KEY")
    else:
        logger.info("GEMINI_API_KEY не задан — совместимость сразу через OpenRouter Pro chain")
    return await _generate_compat_via_openrouter(system_prompt, user_prompt)


def compatibility_report_to_json(report: dict[str, str]) -> str:
    return json.dumps(_normalize_compat_report(report), ensure_ascii=False)


def compatibility_report_from_json(raw: str | None) -> dict[str, str] | None:
    if not raw:
        return None
    try:
        return _normalize_compat_report(_parse_json_object(raw))
    except Exception:
        return None


def format_compatibility_telegram_html(
    report: dict[str, str],
    *,
    user_name: str = "ты",
    partner_name: str = "партнёр",
) -> str:
    import html as html_mod

    parts = [
        f"<b>💞 Композит: {html_mod.escape(user_name)} + {html_mod.escape(partner_name)}</b>",
        f"<b>✨ Магнетизм</b>\n{md_to_telegram_html(report['attraction'])}",
        f"<b>⚡ Зоны трения</b>\n{md_to_telegram_html(report['conflicts'])}",
        f"<b>🌱 Синергия</b>\n{md_to_telegram_html(report['growth'])}",
        f"\n<i>{html_mod.escape(_HD_WATERMARK)}</i>",
    ]
    return "\n\n".join(parts)
