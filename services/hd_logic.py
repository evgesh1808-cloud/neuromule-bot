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
from functools import lru_cache
from pathlib import Path
from typing import Any

import aiosqlite

from config import settings as _app_settings
from services.hd_channel_archetypes import (
    CHANNEL_ARCHETYPE_PROMPT_RULE,
    channels_llm_context_block,
    format_channel_superpower_for_user,
    text_contains_raw_channel_code,
)
from services import hd_chart
from services.hd_profile_archetypes import (
    PROFILE_ARCHETYPE_PROMPT_RULE,
    format_profile_archetype_for_user,
    profile_archetype_label,
    profile_llm_context_lines,
    text_contains_raw_profile_code,
)
from utils.sanitize import sanitize_hd_user_facing_text

try:
    from google import genai
except ImportError:  # pragma: no cover - surfaced at runtime in the handler.
    genai = None

try:
    import swisseph as swe
except ImportError:  # pragma: no cover - surfaced at runtime in the handler.
    swe = None

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
except ImportError:  # pragma: no cover - surfaced at runtime in the handler.
    Image = None  # type: ignore[misc, assignment]
    ImageChops = None  # type: ignore[misc, assignment]
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
    "gemini-3.1-flash-preview",
    "gemini-2.0-flash",
)
_GEMINI_PREMIUM_MODEL_CHAIN: tuple[str, ...] = (
    "gemini-3.1-pro-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
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
_OPENROUTER_PREMIUM_UPGRADE_TIMEOUT_SEC = 75.0
_OPENROUTER_WELCOME_HOOK_TIMEOUT_SEC = 12.0
_WELCOME_HOOK_MAX_TOKENS = 420
_HD_UPGRADE_LLM_TIMEOUT_SEC = 120.0
_HD_REGENERATE_LLM_TIMEOUT_SEC = 180.0
_HD_LLM_PARALLEL_LIMIT = 5
_HD_PREMIUM_MAX_OUTPUT_TOKENS = 8192
_PDF_FONT_NAME = "HDReportFont"
_PDF_FONT_BOLD_NAME = "HDReportFontBold"
_PDF_COVER_BG = "#0D0E12"
_PDF_CONTENT_BG = "#FAFAFA"
_PDF_ACCENT_HEX = _HD_NEON_HEX
_PDF_BODYGRAPH_WIDTH_PX = 430
_PDF_BODYGRAPH_MAX_BYTES = 300 * 1024
_PDF_CHAPTER_SPECS: tuple[tuple[str, str, str], ...] = (
    ("money", "💼 Раздел: Финансовый Аудит", "hd_ch_money"),
    ("love", "❤️ Раздел: Отношения и Партнёрство", "hd_ch_love"),
    ("energy", "⚡ Раздел: Энергетическая Архитектура", "hd_ch_energy"),
    ("plan", "📅 Раздел: План на 30 дней", "hd_ch_plan"),
)
_STATIC_PDF_SECTION_SPECS: tuple[tuple[str, str, str], ...] = (
    ("type", "Тип и стратегия", "hd_static_type"),
    ("profile", "Профиль личности", "hd_static_profile"),
    ("mechanics", "Механика решений", "hd_static_mechanics"),
    ("centers_defined", "Определённые центры", "hd_static_centers_def"),
    ("centers_open", "Открытые центры", "hd_static_centers_open"),
    ("channels", "Активные каналы", "hd_static_channels"),
    ("incarnation_cross", "Инкарнационный крест", "hd_static_cross"),
)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_HD_BLOCKS_ROOT = _PROJECT_ROOT / "data" / "hd_blocks"
_HD_LLM_SEMAPHORE: asyncio.Semaphore | None = None


def _hd_llm_semaphore() -> asyncio.Semaphore:
    """Ограничивает параллельные OpenRouter/Gemini вызовы HD (anti-429 на новых тирах)."""
    global _HD_LLM_SEMAPHORE
    if _HD_LLM_SEMAPHORE is None:
        _HD_LLM_SEMAPHORE = asyncio.Semaphore(_HD_LLM_PARALLEL_LIMIT)
    return _HD_LLM_SEMAPHORE


_PREMIUM_REPORT_KEYS = ("fast_facts", "money", "love", "energy", "plan")
_PREMIUM_EXTENDED_REPORT_KEYS = (
    "genius_light",
    "mars_trauma",
    "false_self_masks",
    "phs_motivation",
    "incarnation_mission",
    "maturity_cycles",
    "dream_rave",
)
_HD_REPORT_SCHEMA_VERSION = 4
_HD_REPORT_SCHEMA_VERSION_SYNTHESIS = 3
_LEGACY_HD_REPORT_PLACEHOLDER = "⚡ Экспресс-анализ доступен в интерактивном разборе."
_FAST_FACTS_MAX_LEN = 2000
_PREMIUM_SUMMARY_TEMPERATURE = 0.1
_PREMIUM_SUMMARY_MAX_TOKENS = 4096
_MAX_SYNTHESIS_PAIRS_FULL = 9
_MAX_SYNTHESIS_PAIRS_UPGRADE = 1
_GENETIC_SYNTHESIS_DOMAINS: frozenset[str] = frozenset({"money", "love", "energy"})
_GENETIC_SYNTHESIS_TEMPERATURE = 0.1
_GENETIC_SYNTHESIS_MAX_TOKENS = 8192
_DOMAIN_CHAPTER_MIN_CHARS = 5000
_DOMAIN_SYNTHESIS_MAX_RETRIES = 2
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


def _hd_premium_llm_tier() -> str:
    """economy — дешёвые модели для тестов; production — Claude Sonnet."""
    return str(getattr(_app_settings, "hd_premium_llm_tier", "economy") or "economy").strip().lower()


def _openrouter_models_for_premium() -> list[str]:
    """OpenRouter premium HD: production = Claude → DeepSeek R1; economy = free/cheap."""
    if _hd_premium_llm_tier() == "production":
        return [
            "anthropic/claude-3.5-sonnet",
            "deepseek/deepseek-r1",
        ]
    return [
        "google/gemini-2.5-flash",
        "google/gemini-3.1-pro-preview",
        "deepseek/deepseek-chat",
    ]


def _openrouter_models_for_premium_upgrade() -> list[str]:
    """Deprecated alias — тот же каскад, что и premium (upgrade-fast снят с prod)."""
    return _openrouter_models_for_premium()


def _openrouter_models_for_welcome_hook() -> list[str]:
    """Welcome-пакет: хлёсткий AI-хук уязвимости за ~1–2 сек."""
    return [
        "openai/gpt-4o",
        "deepseek/deepseek-chat",
    ]


def _build_welcome_vulnerability_hook_prompt(
    user_name: str,
    math_data: dict[str, object],
) -> tuple[str, str]:
    """Промпт Welcome-пакета: один абзац «хук уязвимости» на первом экране."""
    name = (user_name or "").strip() or "друг"
    data = math_data if isinstance(math_data, dict) else {}
    hd_type = str(data.get("hd_type") or "не указан")
    profile_code_line, profile_archetype_line = profile_llm_context_lines(str(data.get("profile") or ""))
    defined, open_centers = _centers_from_math_data(data)
    channels_block = channels_llm_context_block(data.get("active_channels"))

    system_prompt = (
        "Ты — NeuroMule HD Welcome Engine. Задача: за 1–2 секунды выдать один абзац "
        "«хук уязвимости» — хлёсткий, узнаваемый, без воды.\n\n"
        f"{_HD_PREMIUM_TOV_BLOCK}\n\n"
        "ФОРМАТ: один JSON {\"hook\": \"...\"}. hook — 350–600 символов plain text, "
        "начинается с Эффекта Зеркала (живая бытовая сцена). Без #, без сухих кодов профилей/каналов. "
        f"{PROFILE_ARCHETYPE_PROMPT_RULE} {CHANNEL_ARCHETYPE_PROMPT_RULE}"
    )
    user_prompt = (
        f"Клиент: {name}. Тип: {hd_type}.\n"
        f"{profile_code_line}\n"
        f"{profile_archetype_line}\n"
        f"Определённые центры: {', '.join(defined) or 'не переданы'}.\n"
        f"Открытые центры: {', '.join(open_centers) or 'не переданы'}.\n"
        f"{channels_block}\n"
        "Сгенерируй hook — одну ударную правду, от которой невозможно отвести взгляд."
    )
    return system_prompt, user_prompt


async def generate_hd_welcome_vulnerability_hook(
    user_name: str,
    math_data: dict[str, object],
) -> str:
    """
    Welcome-пакет: AI-хук уязвимости для первого экрана (OpenRouter GPT-4o → DeepSeek-V3).
    """
    if not _openrouter_configured():
        raise RuntimeError("hd_welcome_hook_unavailable: задайте OPENROUTER_API_KEY")
    from services.ai_text import ask_ai_messages

    system_prompt, user_prompt = _build_welcome_vulnerability_hook_prompt(user_name, math_data)
    async with _hd_llm_semaphore():
        completion = await ask_ai_messages(
            _app_settings,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            timeout=_OPENROUTER_WELCOME_HOOK_TIMEOUT_SEC,
            models=_openrouter_models_for_welcome_hook(),
            max_tokens=_WELCOME_HOOK_MAX_TOKENS,
            temperature=0.35,
            response_format={"type": "json_object"},
            log_context="hd_welcome_hook",
        )
    text = (completion.get("content") or "").strip()
    if not text:
        raise RuntimeError("hd_welcome_hook_empty")
    parsed = _parse_json_object(text)
    hook = parsed.get("hook")
    if not isinstance(hook, str) or not hook.strip():
        raise ValueError("welcome hook JSON missing non-empty hook")
    cleaned = hook.strip()
    _validate_hd_user_facing_text(cleaned, field="welcome hook")
    if _synthesis_text_has_markdown_headers(cleaned):
        raise ValueError("welcome hook contains markdown headers")
    return cleaned


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


def _normalize_premium_report(
    parsed: dict[str, object],
    *,
    relax_cliches: bool = False,
    active_channels: object = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for key in _PREMIUM_REPORT_KEYS:
        value = parsed.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Gemini JSON response is missing non-empty {key!r}")
        cleaned = sanitize_hd_user_facing_text(
            value.strip(),
            active_channels=active_channels,
        )
        _validate_hd_user_facing_text(
            cleaned,
            field=f"premium report field {key!r}",
            strict_cliches=not relax_cliches,
        )
        report[key] = cleaned
    if len(report["fast_facts"]) > _FAST_FACTS_MAX_LEN:
        report["fast_facts"] = report["fast_facts"][: _FAST_FACTS_MAX_LEN - 1].rstrip() + "…"
    report["energy_scales"] = _normalize_energy_scales(parsed.get("energy_scales"))
    for key in _PREMIUM_EXTENDED_REPORT_KEYS:
        val = parsed.get(key)
        if isinstance(val, str) and val.strip():
            report[key] = sanitize_hd_user_facing_text(val.strip(), active_channels=active_channels)
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
    chart: dict[str, object] = {}
    try:
        if birth_data.strip():
            chart = hd_chart.build_pure_hd_chart(birth_data)
            raw_gates = chart.get("gates")
            if isinstance(raw_gates, dict):
                gates = raw_gates
            defined_set = set(chart.get("defined_centers") or [])
            active_channels = list(chart.get("active_channels") or [])
            derived = {
                "hd_type": str(chart.get("hd_type") or ""),
                "profile": str(chart.get("profile") or ""),
                "authority": str(chart.get("authority") or ""),
                "strategy": str(chart.get("strategy") or ""),
            }
    except Exception:
        logger.debug("build_hd_math_data: ephemeris/chart unavailable", exc_info=True)
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
    domain_synthesis_pairs = hd_chart.build_domain_synthesis_pairs(
        {
            "defined_centers": defined_centers,
            "open_centers": open_centers,
            "active_channels": active_channels,
            "definition": definition,
        }
    )
    return {
        "hd_type": resolved_type,
        "birth_data": birth_data,
        "defined_centers": defined_centers,
        "open_centers": open_centers,
        "gates": gates,
        "profile": derived.get("profile", ""),
        "profile_archetype": str(chart.get("profile_archetype") or ""),
        "authority": derived.get("authority", ""),
        "strategy": derived.get("strategy", ""),
        "definition": definition,
        "active_channels": active_channels,
        "synthesis_pairs": synthesis_pairs,
        "domain_synthesis_pairs": domain_synthesis_pairs,
        "key_activations": chart.get("key_activations") or {},
        "birth_place": chart.get("birth_place") or "",
        "timezone": chart.get("timezone") or "",
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
        "profile_archetype": str(
            math_data.get("profile_archetype")
            or profile_archetype_label(str(math_data.get("profile") or ""))
        ),
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


def _parse_plain_text_hd_report(text: str) -> dict[str, str]:
    """Разбор старых отчётов, сохранённых plain-text (format_premium_report), не JSON."""
    patterns: tuple[tuple[str, str], ...] = (
        ("fast_facts", r"⚡\s*Экспресс-анализ\s*\n(.*?)(?=\n\n💎|\Z)"),
        ("money", r"💎\s*Деньги\s*\n(.*?)(?=\n\n❤️|\Z)"),
        ("love", r"❤️\s*Отношения\s*\n(.*?)(?=\n\n⚡️|\Z)"),
        ("energy", r"⚡️\s*Энергия\s*\n(.*?)(?=\n\n📅|\Z)"),
        ("plan", r"📅\s*План на 30 дней\s*\n(.*?)\Z"),
    )
    sections: dict[str, str] = {}
    for key, pattern in patterns:
        match = re.search(pattern, text, flags=re.DOTALL)
        if match:
            body = match.group(1).strip()
            if body:
                sections[key] = body
    if sum(1 for key in ("money", "love", "energy", "plan") if sections.get(key)) >= 2:
        return sections
    raise ValueError("plain_text_hd_report_unrecognized")


def _parse_hd_report_storage(raw: str) -> dict[str, Any]:
    """JSON или legacy plain-text → dict полей отчёта."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty_hd_report")
    try:
        parsed = _parse_json_object(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return _parse_plain_text_hd_report(text)


def is_legacy_hd_report_raw(raw: str | None) -> bool:
    """True только для отчётов до schema v3 (v3 никогда не legacy, даже с placeholder fast_facts)."""
    if not raw:
        return True
    version = hd_report_schema_version(raw)
    if version >= _HD_REPORT_SCHEMA_VERSION:
        return False
    try:
        parsed = _parse_hd_report_storage(raw)
        fast = str(parsed.get("fast_facts") or "").strip()
        if fast == _LEGACY_HD_REPORT_PLACEHOLDER:
            return True
    except Exception:
        return True
    return version < _HD_REPORT_SCHEMA_VERSION


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
    for key in _PREMIUM_EXTENDED_REPORT_KEYS:
        val = report.get(key)
        if isinstance(val, str) and val.strip():
            payload[key] = val.strip()
    payload["schema_version"] = _HD_REPORT_SCHEMA_VERSION
    return json.dumps(payload, ensure_ascii=False)


def premium_report_from_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = _parse_hd_report_storage(raw)
        return _normalize_premium_report(parsed, relax_cliches=True)
    except Exception:
        logger.debug("premium_report_from_json strict normalize failed, soft load", exc_info=True)
    try:
        parsed = _parse_hd_report_storage(raw)
        if not isinstance(parsed, dict):
            return None
        legacy_keys = ("money", "love", "energy", "plan")
        if all(isinstance(parsed.get(k), str) and str(parsed.get(k)).strip() for k in legacy_keys):
            legacy_report: dict[str, Any] = {
                k: _sanitize_hd_user_facing_text(str(parsed[k]).strip()) for k in legacy_keys
            }
            legacy_report["fast_facts"] = str(parsed.get("fast_facts") or "").strip() or (
                _LEGACY_HD_REPORT_PLACEHOLDER
            )
            legacy_report["energy_scales"] = _normalize_energy_scales(parsed.get("energy_scales"))
            return legacy_report
        report: dict[str, Any] = {}
        for key in _PREMIUM_REPORT_KEYS:
            val = parsed.get(key)
            if isinstance(val, str) and val.strip():
                report[key] = _sanitize_hd_user_facing_text(val.strip())
            elif key == "fast_facts":
                report[key] = _LEGACY_HD_REPORT_PLACEHOLDER
            else:
                report[key] = "Раздел временно недоступен — нажми 🔄 Обновить отчёт."
        report["energy_scales"] = _normalize_energy_scales(parsed.get("energy_scales"))
        for key in _PREMIUM_EXTENDED_REPORT_KEYS:
            val = parsed.get(key)
            if isinstance(val, str) and val.strip():
                report[key] = _sanitize_hd_user_facing_text(val.strip())
        return report
    except Exception:
        logger.exception("premium_report_from_json soft load failed")
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

    fast = str(report.get("fast_facts") or "").strip()
    lead = intro.strip()
    if fast:
        return f"{lead}\n\n{md_to_telegram_html(fast)}"
    type_hint = html_mod.escape(hd_type.strip()) if hd_type else "Human Design"
    return f"{lead}\n\n<b>{type_hint}</b> — выбери раздел ниже или открой интерактивный разбор."


def resolve_user_gender_from_row(user_row) -> str:
    """Пол клиента из SQLite (если колонка есть)."""
    keys = user_row.keys() if hasattr(user_row, "keys") else ()
    for col in ("gender", "user_gender", "sex"):
        if col in keys:
            val = str(user_row[col] or "").strip()
            if val:
                return val
    return ""


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
    math_data = build_hd_math_data(hd_type, birth_data)
    report: dict[str, Any] | None = None
    try:
        report = await asyncio.wait_for(
            generate_premium_report(
                hd_type,
                birth_data,
                user_name=user_name,
                user_gender=resolve_user_gender_from_row(user),
            ),
            timeout=_HD_UPGRADE_LLM_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.error("HD report upgrade LLM timeout uid=%s — offline legacy wrap", user_id)
    except Exception as exc:
        logger.exception("HD report upgrade LLM failed uid=%s: %s", user_id, exc)

    if report is None:
        report = _offline_hd_premium_report(raw, math_data)
        logger.warning("HD report upgrade uid=%s served via offline fallback", user_id)

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


async def _generate_premium_report_legacy_single_prompt(
    user_name: str,
    math_data: dict[str, object],
    *,
    upgrade_mode: bool,
    prior_errors: list[str] | None = None,
    user_gender: str = "",
) -> dict[str, Any]:
    """Один LLM-вызов (OpenRouter → Gemini) без multi-pass synthesis."""
    system_prompt, user_prompt = _build_elite_premium_hd_prompt(
        user_name,
        math_data,
        user_gender=user_gender,
    )
    errors: list[str] = list(prior_errors or [])
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

    if _openrouter_configured():
        try:
            report = await _generate_premium_via_openrouter(
                system_prompt,
                user_prompt,
                models=or_models,
                timeout=or_timeout,
                relax_cliches=upgrade_mode,
                active_channels=math_data.get("active_channels"),
            )
            report["energy_scales"] = compute_energy_scales_from_math(math_data)
            logger.info(
                "HD premium report served via OpenRouter legacy single-prompt upgrade_mode=%s",
                upgrade_mode,
            )
            return report
        except Exception as or_exc:  # noqa: BLE001
            logger.warning("OpenRouter premium report failed, trying Gemini: %s", or_exc)
            errors.append(f"openrouter: {or_exc!r}")
    elif not _gemini_configured():
        raise RuntimeError("hd_premium_unavailable: задайте OPENROUTER_API_KEY или GEMINI_API_KEY")

    if _gemini_configured():
        try:
            report = await _generate_premium_via_gemini(
                system_prompt,
                user_prompt,
                relax_cliches=upgrade_mode,
            )
            report["energy_scales"] = compute_energy_scales_from_math(math_data)
            logger.info(
                "HD premium report served via Gemini legacy single-prompt upgrade_mode=%s",
                upgrade_mode,
            )
            return report
        except Exception as gemini_exc:  # noqa: BLE001
            logger.exception("Gemini premium report fallback failed")
            errors.append(f"gemini: {gemini_exc!r}")
    elif genai is None:
        errors.append("google-genai_missing")

    raise RuntimeError("hd_premium_unavailable: " + "; ".join(errors))


def _wrap_legacy_report_as_v3(
    raw: str,
    math_data: dict[str, object],
) -> dict[str, Any]:
    """
    Последний резерв: сохраняем текст legacy-отчёта, добавляем schema v3 + static-блоки без LLM.
    """
    from services.hd_static_blocks import assemble_static_reference

    parsed = _parse_hd_report_storage(raw) if (raw or "").strip() else {}

    report: dict[str, Any] = {}
    for key in _PREMIUM_REPORT_KEYS:
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            report[key] = _sanitize_hd_user_facing_text(
                value.strip(),
                active_channels=math_data.get("active_channels"),
            )
        elif key == "fast_facts":
            report[key] = _LEGACY_HD_REPORT_PLACEHOLDER
        else:
            report[key] = "Раздел будет доступен после повторной генерации."

    report["energy_scales"] = compute_energy_scales_from_math(math_data)
    static_sections = assemble_static_reference(math_data, gate_to_center=_GATE_TO_CENTER)
    if static_sections:
        report["static_reference"] = static_sections
    report["synthesis_meta"] = {
        "pairs_requested": 0,
        "blocks_ok": 0,
        "blocks_failed": 0,
        "upgrade_offline": True,
    }
    return report


def _offline_hd_premium_report(
    raw: str | None,
    math_data: dict[str, object],
) -> dict[str, Any]:
    """Офлайн-отчёт без LLM: wrap legacy → minimal (не бросает наружу)."""
    existing = (raw or "").strip()
    for factory, label in (
        (_wrap_legacy_report_as_v3, "offline_wrap"),
        (_minimal_hd_report_fallback, "minimal_fallback"),
    ):
        try:
            wrapped = factory(existing, math_data)
            logger.warning("HD offline report served via %s", label)
            return wrapped
        except Exception:
            logger.exception("HD offline report %s failed", label)
    logger.warning("HD offline report served via minimal_fallback (guaranteed)")
    return _minimal_hd_report_fallback(existing, math_data)


def _minimal_hd_report_fallback(
    raw: str,
    math_data: dict[str, object],
) -> dict[str, Any]:
    """Абсолютный резерв: хотя бы schema v3 + static, чтобы апгрейд никогда не падал в UI."""
    from services.hd_static_blocks import assemble_static_reference

    preview = re.sub(r"\s+", " ", (raw or "").strip())[:1200]
    report: dict[str, Any] = {
        "fast_facts": _LEGACY_HD_REPORT_PLACEHOLDER,
        "money": preview or "Раздел временно недоступен — откройте поддержку.",
        "love": "Раздел будет обновлён при следующей успешной AI-генерации.",
        "energy": "Раздел будет обновлён при следующей успешной AI-генерации.",
        "plan": "Раздел будет обновлён при следующей успешной AI-генерации.",
        "energy_scales": compute_energy_scales_from_math(math_data),
        "synthesis_meta": {"upgrade_offline": True, "upgrade_minimal": True},
    }
    try:
        static_sections = assemble_static_reference(math_data, gate_to_center=_GATE_TO_CENTER)
        if static_sections:
            report["static_reference"] = static_sections
    except Exception:
        logger.exception("minimal_hd_report_fallback static_reference failed")
    return report


async def generate_premium_report(
    hd_type: str,
    birth_data: str,
    *,
    user_name: str = "друг",
    user_gender: str = "",
    upgrade_mode: bool = False,
) -> dict[str, Any]:
    """Полный HD-разбор: parallel multi-pass Genetic Synthesis → legacy fallback."""
    _ = upgrade_mode  # upgrade-fast снят; legacy апгрейды идут через multipass
    math_data = build_hd_math_data(hd_type, birth_data)
    ql_exc: BaseException | None = None
    try:
        report = await _generate_premium_report_multipass(
            user_name,
            math_data,
            user_gender=user_gender,
        )
        logger.info("HD premium report served via Quiet Luxury multipass")
        return report
    except Exception as exc:  # noqa: BLE001
        ql_exc = exc
        logger.warning("Quiet Luxury multipass failed, legacy synthesis fallback: %s", ql_exc)
    try:
        report = await _generate_premium_report_multipass_legacy(
            user_name,
            math_data,
            user_gender=user_gender,
        )
        logger.info("HD premium report served via legacy Genetic Synthesis multipass")
        return report
    except Exception as legacy_exc:  # noqa: BLE001
        logger.warning("Legacy multipass failed, single-prompt fallback: %s", legacy_exc)
        return await _generate_premium_report_legacy_single_prompt(
            user_name,
            math_data,
            upgrade_mode=False,
            prior_errors=[f"quiet_luxury: {ql_exc!r}", f"legacy: {legacy_exc!r}"],
            user_gender=user_gender,
        )


async def generate_premium_report_resilient(
    hd_type: str,
    birth_data: str,
    *,
    user_name: str = "друг",
    user_gender: str = "",
    existing_raw: str | None = None,
    timeout_sec: float = _HD_REGENERATE_LLM_TIMEOUT_SEC,
) -> tuple[dict[str, Any], bool]:
    """
    Полный HD-разбор с офлайн-fallback (как ensure_modern_hd_report).

    Returns:
        (report, llm_ok) — llm_ok=False, если отдан wrap/minimal без живого LLM.
    """
    math_data = build_hd_math_data(hd_type, birth_data)
    raw = (existing_raw or "").strip()
    try:
        if not _openrouter_configured() and not _gemini_configured():
            logger.warning("HD resilient: LLM keys missing — immediate offline report")
            return _offline_hd_premium_report(raw, math_data), False

        report: dict[str, Any] | None = None
        try:
            report = await asyncio.wait_for(
                generate_premium_report(
                    hd_type,
                    birth_data,
                    user_name=user_name,
                    user_gender=user_gender,
                ),
                timeout=timeout_sec,
            )
            if report:
                return report, True
        except asyncio.TimeoutError:
            logger.error(
                "HD premium report timeout uid_birth=%r timeout_sec=%s",
                birth_data[:60],
                timeout_sec,
            )
        except Exception:
            logger.exception("HD premium report resilient primary path failed")

        return _offline_hd_premium_report(raw, math_data), False
    except Exception:
        logger.exception("HD premium report resilient catastrophic fallback")
        return _offline_hd_premium_report(raw, math_data), False


def _parse_premium_report_from_llm(
    raw: str,
    *,
    relax_cliches: bool = False,
    active_channels: object = None,
) -> dict[str, Any]:
    parsed = _parse_json_object(raw)
    return _normalize_premium_report(
        parsed,
        relax_cliches=relax_cliches,
        active_channels=active_channels,
    )


def _parse_compat_report_from_llm(raw: str) -> dict[str, str]:
    parsed = _parse_json_object(raw)
    return _normalize_compat_report(parsed)


async def _generate_premium_via_gemini(
    system_prompt: str,
    user_prompt: str,
    *,
    relax_cliches: bool = False,
) -> dict[str, str]:
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
            report = _parse_premium_report_from_llm(
                _extract_gemini_text(response),
                relax_cliches=relax_cliches,
            )
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
                return _parse_premium_report_from_llm(
                    _extract_gemini_text(response),
                    relax_cliches=relax_cliches,
                )
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
    relax_cliches: bool = False,
    active_channels: object = None,
) -> dict[str, Any]:
    from services.ai_text import ask_ai_messages

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    async with _hd_llm_semaphore():
        completion = await ask_ai_messages(
            _app_settings,
            messages,
            timeout=timeout if timeout is not None else _OPENROUTER_PREMIUM_TIMEOUT_SEC,
            models=models or _openrouter_models_for_premium(),
            max_tokens=_HD_PREMIUM_MAX_OUTPUT_TOKENS,
            temperature=_GENETIC_SYNTHESIS_TEMPERATURE,
            response_format={"type": "json_object"},
            log_context="hd_premium_report",
        )
    text = (completion.get("content") or "").strip()
    if not text:
        raise RuntimeError("openrouter_premium_report_empty")
    return _parse_premium_report_from_llm(
        text,
        relax_cliches=relax_cliches,
        active_channels=active_channels,
    )


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
    return hd_chart.extract_birth_numbers(raw)


def calculate_bodygraph_snapshot(birth_data: str) -> dict[str, float | str]:
    chart = hd_chart.build_pure_hd_chart(birth_data)
    personality = chart.get("personality_gates")
    snapshot: dict[str, float | str] = {
        "birth_data": birth_data.strip(),
        "julian_day": float(chart.get("personality_jd") or 0.0),
    }
    if isinstance(personality, dict):
        for name, payload in personality.items():
            if isinstance(payload, dict):
                lon = payload.get("longitude")
                if isinstance(lon, (int, float)):
                    snapshot[name] = round(float(lon), 6)
    return snapshot


def _longitude_to_gate(longitude: float) -> dict[str, int | float]:
    return hd_chart.longitude_to_gate(longitude)


def get_calculated_gates(birth_data: str) -> dict[str, object]:
    chart = hd_chart.build_pure_hd_chart(birth_data)
    gates = chart.get("gates")
    return {
        "birth_data": chart.get("birth_data") or birth_data.strip(),
        "julian_day": chart.get("personality_jd"),
        "gates": gates if isinstance(gates, dict) else {},
    }


def derive_hd_chart_from_birth(birth_data: str) -> dict[str, str]:
    """Swiss Ephemeris: тип, профиль, авторитет, стратегия для легенды PDF и Stories."""
    chart = hd_chart.build_pure_hd_chart(birth_data)
    return {
        "hd_type": str(chart.get("hd_type") or ""),
        "profile": str(chart.get("profile") or ""),
        "authority": str(chart.get("authority") or ""),
        "strategy": str(chart.get("strategy") or ""),
    }

_HD_TYPE_STRATEGIES: dict[str, str] = hd_chart.HD_TYPE_STRATEGIES


def _infer_hd_type_from_centers(defined: set[str]) -> str:
    return hd_chart.infer_hd_type_from_centers(defined)


def _infer_authority_from_centers(defined: set[str]) -> str:
    return hd_chart.infer_authority_from_centers(defined)


def _strategy_for_hd_type(hd_type: str) -> str:
    return hd_chart.strategy_for_hd_type(hd_type)


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
        str(_PROJECT_ROOT / "assets" / "fonts" / "Roboto-Regular.ttf"),
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


def ensure_pdf_fonts_available() -> None:
    """Pre-flight: кириллические TTF для PDF (как assets/fonts для Stories)."""
    if _find_pdf_font() is None:
        raise RuntimeError(
            "HD PDF fonts missing on disk: expected "
            f"{_PROJECT_ROOT / 'assets' / 'fonts' / 'Roboto-Regular.ttf'}"
        )


def _find_pdf_font_bold(regular_path: str | None) -> str | None:
    if regular_path:
        sibling = Path(regular_path).parent / "Roboto-Bold.ttf"
        if sibling.is_file():
            return str(sibling)
    candidates = [
        str(_PROJECT_ROOT / "assets" / "fonts" / "Roboto-Bold.ttf"),
        str(_PROJECT_ROOT / "fonts" / "Roboto-Bold.ttf"),
    ]
    for item in candidates:
        if item and Path(item).exists():
            return item
    return None


def _register_pdf_font() -> str:
    if pdfmetrics is None or TTFont is None:
        raise RuntimeError("Установите пакет reportlab для PDF-отчетов.")
    ensure_pdf_fonts_available()
    font_path = _find_pdf_font()
    if not font_path:
        raise RuntimeError("HD PDF font path unresolved after pre-flight")
    registered = pdfmetrics.getRegisteredFontNames()
    if _PDF_FONT_NAME not in registered:
        pdfmetrics.registerFont(TTFont(_PDF_FONT_NAME, font_path))
    bold_path = _find_pdf_font_bold(font_path)
    if bold_path and _PDF_FONT_BOLD_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(_PDF_FONT_BOLD_NAME, bold_path))
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
    return hd_chart.derive_active_channels(gate_numbers)


def derive_defined_centers_from_gates(gate_numbers: set[int]) -> set[str]:
    return hd_chart.derive_defined_centers_from_gates(gate_numbers)


def derive_definition_type(defined_centers: set[str], active_channels: list[str]) -> str:
    return hd_chart.derive_definition_type(defined_centers, active_channels)


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


def _read_hd_blocks_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("load_static_block: failed to read %s", path, exc_info=True)
        return None


@lru_cache(maxsize=256)
def load_static_block(folder: str, block_id: str) -> dict[str, Any]:
    """
    Лениво читает JSON-блок из ``data/hd_blocks/`` (0 LLM).

    Examples:
        load_static_block("types", "generator")
        load_static_block("centers/defined", "sacral")
        load_static_block("gates", "41")
        load_static_block("channels", "20-34")
        load_static_block("profiles", "5/1")
    """
    folder_key = (folder or "").strip().strip("/\\")
    block_key = (block_id or "").strip()
    if not folder_key or not block_key:
        return {}

    if folder_key in {"gates", "channels", "profiles"}:
        index_path = _HD_BLOCKS_ROOT / f"{folder_key}.json"
        index = _read_hd_blocks_json(index_path)
        if isinstance(index, dict):
            entry = index.get(block_key)
            if isinstance(entry, dict):
                return entry
        return {}

    file_path = _HD_BLOCKS_ROOT / folder_key / f"{block_key}.json"
    data = _read_hd_blocks_json(file_path)
    return data if isinstance(data, dict) else {}


_STATIC_DOMAIN_TITLES: dict[str, str] = {
    "money": "💼 Финансовая механика карты",
    "love": "❤️ Отношения и близость",
    "energy": "⚡ Энергетическая архитектура",
}


def _compose_static_domain_chapter(domain: str, static_context: str) -> str:
    """Глава отчёта только из disk static blocks (0 LLM)."""
    title = _STATIC_DOMAIN_TITLES.get(domain, domain)
    static = static_context.strip()
    if not static:
        return f"{title}\n\nСтатическая база карты недоступна."
    return f"{title}\n\n{static}"


def _resolve_static_sections_for_pdf(
    report: dict[str, Any],
    math_data: dict[str, object],
) -> dict[str, str]:
    from services.hd_static_blocks import assemble_static_reference

    _ = report
    return assemble_static_reference(math_data, gate_to_center=_GATE_TO_CENTER)


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
        chart = hd_chart.build_pure_hd_chart(birth_data)
        return set(chart.get("defined_centers") or []), None
    except Exception as exc:
        return set(), f"Схема не рассчитана: {exc}"


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

_ELITE_HD_BANNED_COACHING_CLICHES: tuple[str, ...] = (
    "слушай себя",
    "слушай тело",
    "слушай своё тело",
    "ставь границы",
    "уважай пространство",
    "уважай своё пространство",
    "верь в себя",
    "просто верь",
    "будь собой",
    "отпусти контроль",
    "позволь себе",
    "ты достоин",
    "ты достойна",
    "работай над собой",
    "пространство",
    "вибраци",
    "осознай",
    "осознайте",
    "практикуй",
    "практикуйте",
    "доверься сигналам",
    "доверься своим сигналам",
    "неделя 1",
    "неделя 2",
    "неделя 3",
    "неделя 4",
)

_HD_MIRROR_EFFECT_RULE = (
    "ЭФФЕКТ ЗЕРКАЛА (обязательно): каждый раздел боли начинай с узнаваемой бытовой сцены — "
    "конкретное место, время суток, телесное ощущение, микродействие. Пример: «Ты проверяешь "
    "рабочую почту в 23:00 с гудящими от усталости ногами, потому что боишься показаться "
    "некомпетентным». Без абстрактных формулировок боли."
)

_HD_PREMIUM_TOV_BLOCK = (
    "TONE OF VOICE — NEUROMULE HD WORLD CLASS:\n"
    "Ты — провокационный психоаналитик и бизнес-стратег в одном лице. Пишешь сочно, "
    "кинематографично, местами жёстко и иронично по отношению к ментальным ловушкам ума. "
    "Сильные русские глаголы, точные метафоры, ноль бюрократии и роботизированности.\n"
    "ЗАПРЕЩЕНЫ плоские коучинговые штампы и клише: «слушай себя», «ставь границы», "
    "«уважай пространство», «верь в себя», «слушай тело», «работай над собой» — "
    "и любые их вариации.\n"
    f"{_HD_MIRROR_EFFECT_RULE}"
)

_HD_BOLD_CHALLENGES_PLAN_RULE = (
    "ПЛАН НА 30 ДНЕЙ = «ДЕРЗКИЕ ВЫЗОВЫ ДЛЯ УМА» (не сухой to-do list):\n"
    "Три блока: дни 1–5 / 6–15 / 16–30. В каждом — 1–2 дерзких вызова по SMART "
    "(Specific, Measurable, Achievable, Relevant, Time-bound): конкретное действие, "
    "измеримая метрика, дедлайн. Обязателен соматический стоп-сигнал: какое телесное "
    "ощущение = «стоп, это Ложное Я» (например: сжатие в диафрагме, ком в горле, "
    "ускоренное дыхание). Без фраз «делай паузу» без контекста."
)

_HD_SYNTHESIS_EXPERIMENTS_RULE = (
    "experiments[] — это «Дерзкие вызовы для ума», не скучные упражнения. "
    "Каждый пункт SMART + соматический маркер стоп-сигнала в success_criteria или metric."
)

_GENETIC_SYNTHESIS_BANNED_MARKERS: tuple[str, ...] = _ELITE_HD_BANNED_MARKERS

_GENETIC_SYNTHESIS_BANNED_ANGLICISMS: tuple[str, ...] = (
    "struggle",
    "willpower",
    "correction",
    "grace",
    "manifestation",
    "awareness",
    "mutation",
    "charisma",
    "preservation",
    "initiation",
    "transitoriness",
    "abstraction",
    "maturation",
    "surrender",
    "brainwave",
    "discovery",
    "recognition",
    "transformation",
    "community",
    "emoting",
    "witness",
    "talent",
    "curiosity",
    "openness",
    "alpha",
    "power",
    "logic",
    "rhythm",
    "intimacy",
    "leadership",
    "concentration",
    "awakening",
    "exploration",
    "money",
    "structuring",
    "judgment",
    "risk taker",
    "commitment",
    "desire",
    "retreat",
    "progress",
    "crisis",
    "friendship",
    "fighter",
    "provocateur",
    "aloneness",
    "fantasy",
    "growth",
    "insight",
    "alertness",
    "gatherer",
    "determination",
    "realization",
    "depth",
    "principles",
    "values",
    "shock",
    "stillness",
    "beginnings",
    "ambition",
    "spirit",
    "stimulation",
    "intuitive clarity",
    "joy of life",
    "sexuality",
    "limitation",
    "mystery",
    "detail",
    "doubt",
    "confusion",
)


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
            "АКЦЕНТ ТИПА: жёсткий бизнес-стратег. Режь воду, бей в KPI и дисциплину исполнения — "
            "но через живые сцены, не через корпоративный канцелярит."
        )
    if any(token in normalized for token in ("проектор", "рефлектор")):
        return (
            "АКЦЕНТ ТИПА: глубокий психоаналитик. Границы, циклы ожидания, распознавание чужих "
            "ожиданий — через зеркало быта и тело, без мистики."
        )
    return "АКЦЕНТ ТИПА: премиальная терапевтическая прямота — конкретика, ответственность, измеримость."


_ELITE_HD_FEW_SHOT = (
    "ПРИМЕР ПЛОТНОСТИ И СТИЛЯ (few-shot, не копируй факты — только плотность и тон):\n"
    '{"fast_facts": "⚡ Главный баг прошивки: доказываешь ценность через переработку. '
    '💼 Триггер больших денег: продавать только после телесного «да». '
    '🔋 Идеальная перезагрузка: сон без будильника + прогулка без цели.", '
    '"money": "Боль\\nВ 23:07 ты снова открываешь почту — ноги гудят, а палец сам жмёт «отправить», '
    'потому что тишина кажется доказательством некомпетентности.\\n\\n'
    'Что делать\\n**Неделя 1:** перед каждым финансовым «да» записывай, где в теле сжимается.", '
    '"love": "Боль\\nТы ловишь себя на том, что киваешь партнёру, уже не слыша вопрос.", '
    '"energy": "Боль\\nЖмёшь газ, когда Сакрал уже пуст — как будто стыд сильнее усталости.", '
    '"plan": "Дни 1–5 — Дерзкий вызов: 5 финансовых решений с записью телесного стоп-сигнала '
    '(метрика: 5 записей, стоп = сжатие в диафрагме)."}'
)

_ELITE_HD_SERVER_MATH_MANDATE = (
    "Ты получаешь точные, математически рассчитанные на сервере данные бодиграфа пользователя "
    "(тип, профиль, закрашенные и открытые центры). Тебе категорически ЗАПРЕЩЕНО самостоятельно "
    "рассчитывать, угадывать или изменять тип личности пользователя. Ты должен строго взять "
    "переданный тип личности и провести его глубокую коучинговую расшифровку по нашему JSON-контракту."
)


def _build_elite_premium_hd_prompt(
    user_name: str,
    math_data: dict,
    *,
    user_gender: str = "",
) -> tuple[str, str]:
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
    channels_block = channels_llm_context_block(data.get("active_channels"))

    banned = ", ".join(f"«{word}»" for word in _ELITE_HD_BANNED_MARKERS[:8])
    cliches = ", ".join(f"«{word}»" for word in _ELITE_HD_BANNED_COACHING_CLICHES[:6])

    gender_block = _hd_gender_prompt_block(user_gender)

    system_prompt = (
        "Ты — ведущий аналитик NeuroMule HD: провокационная глубинная психотерапия + "
        "бизнес-консалтинг без эзотерики. Пишешь премиальный персональный разбор — "
        "поведение, паттерны, деньги, отношения, энергия.\n\n"
        f"{gender_block}\n\n"
        f"{_HD_PREMIUM_TOV_BLOCK}\n\n"
        f"{tone_block}\n\n"
        f"{_ELITE_HD_SERVER_MATH_MANDATE}\n\n"
        "ЖЁСТКИЕ ЗАПРЕТЫ:\n"
        f"- Не используй: {banned}, «вибрации», «карма», «космос», «вселенная посылает», "
        "«астрал», «судьба», «предназначение-сверху», «нейтрино».\n"
        f"- Запрещены коучинговые клише: {cliches} и их вариации.\n"
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
        "Каналы и ворота — только как «Суперсила: …», БЕЗ кодов вида '34-20', '19-49'. "
        "Без ### и # — только plain text, эмодзи и **жирный**.\n"
        "- energy_scales: три целых числа 1–100 — capacity (ёмкость ауры по моторам), "
        "immunity (стойкость к чужому мнению по открытым центрам), scale (индекс харизмы/влияния).\n"
        "- money, love, energy: plain text с подзаголовками «Боль» и «Что делать». "
        "«Боль» начинается с Эффекта Зеркала (живая сцена). "
        "Для **жирного акцента** используй только парные **звёздочки** (без ### и #). "
        "КАЖДЫЙ раздел начинается с честной психологической боли из-за Ложного Я этой механики. "
        "Объём каждого раздела — от 2500 до 6000 символов.\n"
        "- ГЕНЕТИЧЕСКИЙ СИНТЕЗ (обязательно): каждый открытый центр описывай только в жёсткой "
        "связке с определёнными моторами и суперсилами каналов клиента.\n"
        f"- {_HD_BOLD_CHALLENGES_PLAN_RULE}\n"
        f"- {PROFILE_ARCHETYPE_PROMPT_RULE}\n"
        f"- {CHANNEL_ARCHETYPE_PROMPT_RULE}\n"
        "Каждый раздел — плотный, без воды; в каждом есть ответ «что делать дальше»."
    )

    profile_archetype = str(data.get("profile_archetype") or profile_archetype_label(profile))
    profile_code_line, profile_archetype_line = profile_llm_context_lines(profile)
    if profile_archetype and profile_archetype not in profile:
        profile_archetype_line = f"- Архетип (человеческий язык): {profile_archetype}"

    user_prompt = (
        f"Клиент: {name}. Обращайся к {name} на «ты».\n"
        f"{gender_block}\n\n"
        "МАТЕМАТИЧЕСКИ ЗАФИКСИРОВАННЫЕ ФАКТЫ (истина, не оспаривай и не дополняй):\n"
        f"- Тип HD: {hd_type}\n"
        f"- Дата/время/место рождения: {birth_data}\n"
        f"- Стратегия: {strategy or 'не передана'}\n"
        f"- Авторитет: {authority or 'не передан'}\n"
        f"{profile_code_line}\n"
        f"{profile_archetype_line}\n"
        f"- Определённые (закрашенные) центры: {defined_line}\n"
        f"- Открытые (незакрашенные) центры: {open_line}\n"
        f"{channels_block}\n"
        f"- {gates_block}\n\n"
        "Сгенерируй JSON-разбор, ювелирно согласованный с определёнными и открытыми центрами выше. "
        "Если центр открыт — не описывай его как постоянный ресурс. "
        "Если центр определён — не называй его зоной уязвимости из-за «отсутствия энергии». "
        "Используй переданный тип личности дословно во всех рекомендациях — без пересчёта и без замены."
    )
    return system_prompt, user_prompt


_GENETIC_SYNTHESIS_FEW_SHOT = (
    "ПРИМЕР ПЛОТНОСТИ JSON (few-shot — не копируй факты, только структуру и тон):\n"
    '{"synthesis_anchor": "Ловушка Доказывания (Открытое Эго × Определённый Сакрал × '
    'архетип Экспериментатор-Спасатель × Суперсила влияния в моменте)", '
    '"client_pain": "В 23:07 ты снова открываешь почту — ноги гудят, а палец жмёт «отправить», '
    'потому что тишина кажется доказательством некомпетентности.", '
    '"false_self_pattern": "Ум подменяет уязвимость Эго перегрузкой Сакрала.", '
    '"body_signal": "Сжатие в диафрагме и ускоренное дыхание перед подписанием договора.", '
    '"reflective_questions": ["Что меняется в теле, если отложить «да» на 24 часа?", '
    '"Какую скрытую выгоду ты получаешь, доказывая ценность перегрузом?", '
    '"Какой минимальный шаг вернёт тебе право выбора без самопредательства?"], '
    '"experiments": [{"timeframe": "days_1-5", "action": "Дерзкий вызов: перед каждым финансовым «да» '
    'записывай телесный стоп-сигнал", "metric": "5 решений + описание стоп-сигнала (сжатие в диафрагме)", '
    '"success_criteria": "5 записей с различимым телесным паттерном до подписания"}]}'
)


def _format_active_channels_line(active_channels: object) -> str:
    """Legacy one-liner; для промптов предпочтителен channels_llm_context_block()."""
    if isinstance(active_channels, list) and active_channels:
        labels = [
            format_channel_superpower_for_user(str(ch))
            for ch in active_channels
            if str(ch).strip()
        ]
        if labels:
            return ", ".join(labels)
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
    user_gender: str = "",
    extra_user_instruction: str = "",
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

    profile = str(data.get("profile") or "").strip()
    profile_code_line, profile_archetype_line = profile_llm_context_lines(profile)
    authority = str(data.get("authority") or "").strip() or "не передан"
    strategy = str(data.get("strategy") or "").strip() or "не передана"
    definition = str(data.get("definition") or "").strip() or "не передана"
    active_channels_line = _format_active_channels_line(data.get("active_channels"))
    channels_block = channels_llm_context_block(data.get("active_channels"))
    scales_line = _format_energy_scales_line(energy_scales)

    banned = ", ".join(f"«{word}»" for word in _GENETIC_SYNTHESIS_BANNED_MARKERS[:10])
    cliches = ", ".join(f"«{word}»" for word in _ELITE_HD_BANNED_COACHING_CLICHES[:6])
    anglicisms = ", ".join(
        term.title() for term in _GENETIC_SYNTHESIS_BANNED_ANGLICISMS[:12]
    )
    domain_ru = {"money": "деньги", "love": "отношения", "energy": "энергия"}[normalized_domain]
    gender_block = _hd_gender_prompt_block(user_gender)

    system_prompt = (
        "Контекст: Ты — ИИ-движок NeuroMule HD на OpenRouter-конвейере мирового уровня. "
        "Создаёшь глубокое, трансформационное психологическое исследование — текст читается "
        "взахлёб даже жёсткому скептику.\n\n"
        f"{gender_block}\n\n"
        f"{_HD_PREMIUM_TOV_BLOCK}\n\n"
        "СТРОГИЕ АРХИТЕКТУРНЫЕ ЗАПРЕТЫ:\n"
        "1. ЗАПРЕТ АНГЛИЦИЗМОВ И ЖАРГОНА: Категорически запрещено использовать сырые английские "
        f"термины из старых баз ({anglicisms} и т.п.). Переводи ВСЁ на богатый, сильный русский язык.\n"
        f"2. ЗАПРЕТ ЭЗОТЕРИЧЕСКОЙ ВОДЫ: Исключи слова-маркеры: {banned}. "
        "Заменяй их на психологические понятия («компенсаторные стратегии», "
        "«сценарии дефицитарности», «соматические маркеры»).\n"
        f"3. ЗАПРЕТ КОУЧИНГОВЫХ КЛИШЕ: {cliches} и их вариации.\n"
        "4. ТОТАЛЬНЫЙ ЗАПРЕТ НА ГАЛЛЮЦИНАЦИИ КАНАЛОВ: Тебе запрещено упоминать, придумывать "
        "или предполагать наличие любых ворот или каналов, которых нет в списках active_channels.\n"
        "5. ЗАПРЕТ MARKDOWN-ЗАГОЛОВКОВ В JSON: Внутри текстовых полей JSON строго запрещено "
        "использовать символы #, ##, ###. Для разделения абзацев используй только \\n.\n"
        "6. ТЕМПЕРАТУРА 0.1: Будь точен, глубок, пиши короткими, бьющими в цель предложениями.\n"
        f"7. {PROFILE_ARCHETYPE_PROMPT_RULE}\n"
        f"8. {CHANNEL_ARCHETYPE_PROMPT_RULE}\n\n"
        "МЕТОДОЛОГИЯ МИРОВОГО СИНТЕЗА:\n"
        "- Фокусируйся на Фрактальном Конфликте. Показывай связку: "
        "«Как твоё Открытое [центр] крадёт чистую энергию твоего Определённого [мотор], чтобы "
        "доказать миру ценность, и почему твой архетип заставляет совершать цикличные ошибки».\n"
        f"- {_HD_MIRROR_EFFECT_RULE}\n"
        "- Телесный (соматический) интеллект: опиши физический маркер (ком в горле, зажим "
        "в диафрагме), по которому человек поймает себя на Ложном Я.\n"
        f"- {_HD_SYNTHESIS_EXPERIMENTS_RULE}\n\n"
        f"ОБЪЁМ: суммарно все текстовые поля JSON (synthesis_anchor + client_pain + "
        f"false_self_pattern + body_signal + questions + experiments) — "
        f"не менее {_DOMAIN_CHAPTER_MIN_CHARS} символов. client_pain ОБЯЗАН начинаться "
        "с кинематографичной сцены и времени суток (Эффект Зеркала).\n\n"
        f"{_GENETIC_SYNTHESIS_FEW_SHOT}\n\n"
        "ВЫДАЧА: строго один JSON-объект без markdown-обёртки ```:\n"
        '{"synthesis_anchor": "Понятная формулировка связки с архетипом и суперсилой канала", '
        '"client_pain": "...", "false_self_pattern": "...", "body_signal": "...", '
        '"reflective_questions": ["...", "...", "..."], '
        '"experiments": [{"timeframe": "days_1-5", "action": "...", "metric": "...", '
        '"success_criteria": "..."}, {"timeframe": "days_6-15", ...}, '
        '{"timeframe": "days_16-30", ...}]}'
    )

    user_prompt = (
        "Входные данные (Верифицированные факты с Python-сервера):\n"
        f"- Сфера жизни: {normalized_domain} ({domain_ru})\n"
        f"{gender_block}\n"
        f"{profile_code_line}\n"
        f"{profile_archetype_line}\n"
        f"- Внутренний Авторитет: {authority}\n"
        f"- Стратегия Типа: {strategy}\n"
        f"- Тип определенности (Definition): {definition}\n"
        f"{channels_block}\n"
        f"- Сводка суперсил (read-only): {active_channels_line}\n"
        f"- Синтез-пара для текущего анализа: Открытый центр [{open_center}] × "
        f"Определённые моторы/якоря {anchors}\n"
        f"- Серверные шкалы энергии (Read-Only): {scales_line}\n\n"
        "Сгенерируй JSON по схеме из system-инструкции. "
        "Любое отклонение от структуры JSON, англицизмы, запрещённые слова, сырые коды профилей/каналов "
        "или символы # приведёт к ошибке валидации."
    )
    if extra_user_instruction.strip():
        user_prompt = f"{user_prompt}\n\n{extra_user_instruction.strip()}"
    return system_prompt, user_prompt


def _synthesis_text_has_markdown_headers(text: str) -> bool:
    return bool(re.search(r"^#{1,6}\s", text or "", flags=re.MULTILINE))


def _synthesis_text_banned_hits(text: str) -> list[str]:
    lowered = (text or "").lower()
    hits = [marker for marker in _GENETIC_SYNTHESIS_BANNED_MARKERS if marker in lowered]
    for term in _GENETIC_SYNTHESIS_BANNED_ANGLICISMS:
        if re.search(rf"\b{re.escape(term.lower())}\b", lowered):
            hits.append(term)
    return hits


def _hd_text_coaching_cliche_hits(text: str) -> list[str]:
    lowered = (text or "").lower()
    return [phrase for phrase in _ELITE_HD_BANNED_COACHING_CLICHES if phrase in lowered]


def _sanitize_hd_user_facing_text(
    text: str,
    *,
    active_channels: object = None,
) -> str:
    return sanitize_hd_user_facing_text(text, active_channels=active_channels)


def _hd_gender_prompt_block(user_gender: str) -> str:
    normalized = (user_gender or "").strip().lower()
    if normalized in {"f", "female", "жен", "женский", "ж", "woman", "w"}:
        return (
            "ПОЛ КЛИЕНТА: женский. Строго соблюдай женский род во всём тексте "
            "(устала, одна, готова, сделала, уверена)."
        )
    if normalized in {"m", "male", "муж", "мужской", "м", "man"}:
        return (
            "ПОЛ КЛИЕНТА: мужской. Строго соблюдай мужской род во всём тексте "
            "(устал, один, готов, сделал, уверен)."
        )
    return (
        "ПОЛ КЛИЕНТА: не указан. Используй нейтральные формулировки на «ты» "
        "без явного рода там, где это возможно."
    )


def _validate_hd_user_facing_text(text: str, *, field: str, strict_cliches: bool = True) -> None:
    if text_contains_raw_profile_code(text):
        raise ValueError(f"{field} contains raw profile code (use archetype)")
    if text_contains_raw_channel_code(text):
        raise ValueError(f"{field} contains raw channel code (use superpower label)")
    cliches = _hd_text_coaching_cliche_hits(text)
    if not cliches:
        return
    if strict_cliches:
        raise ValueError(f"{field} contains banned coaching cliches: {cliches[:2]}")
    logger.warning("HD text soft cliche hit field=%s: %s", field, cliches[:2])


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
        _validate_hd_user_facing_text(cleaned, field=f"synthesis field {key!r}")
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
        _validate_hd_user_facing_text(q, field=f"synthesis reflective_questions[{idx}]")
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
            _validate_hd_user_facing_text(exp[field], field=f"synthesis experiment[{idx}].{field}")
        experiments.append(exp)
    report["experiments"] = experiments
    return report


def _parse_synthesis_response_from_llm(raw: str) -> dict[str, Any]:
    parsed = _parse_json_object(raw)
    return _normalize_synthesis_response(parsed)


def render_synthesis_block(synthesis: dict[str, Any]) -> str:
    """Plain-text фрагмент главы из JSON Genetic Synthesis."""
    tf_labels = {
        "days_1-5": "Дни 1–5",
        "days_6-15": "Дни 6–15",
        "days_16-30": "Дни 16–30",
    }
    parts: list[str] = [
        "**Якорь синтеза**",
        str(synthesis.get("synthesis_anchor") or "").strip(),
        "",
        "**Сцена боли**",
        str(synthesis.get("client_pain") or "").strip(),
        "",
        "**Паттерн Ложного Я**",
        str(synthesis.get("false_self_pattern") or "").strip(),
        "",
        "**Соматический маркер**",
        str(synthesis.get("body_signal") or "").strip(),
        "",
        "**Вопросы для исследования**",
    ]
    for question in synthesis.get("reflective_questions") or []:
        parts.append(f"- {question}")
    parts.append("")
    parts.append("**Практические наблюдения**")
    for experiment in synthesis.get("experiments") or []:
        if not isinstance(experiment, dict):
            continue
        tf = str(experiment.get("timeframe") or "")
        label = tf_labels.get(tf, tf)
        parts.append(
            f"**{label}**\n{experiment.get('action', '')}\n"
            f"Метрика: {experiment.get('metric', '')}\n"
            f"Критерий успеха: {experiment.get('success_criteria', '')}"
        )
    return "\n".join(parts).strip()


async def _premium_llm_markdown_call(system_prompt: str, user_prompt: str) -> str:
    """Markdown-глава premium-отчёта (Quiet Luxury, без JSON mode)."""
    from services.ai_text import ask_ai_messages

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    completion = await ask_ai_messages(
        _app_settings,
        messages,
        timeout=_OPENROUTER_PREMIUM_TIMEOUT_SEC,
        models=_openrouter_models_for_premium(),
        max_tokens=_GENETIC_SYNTHESIS_MAX_TOKENS,
        temperature=0.15,
        log_context="hd_premium_chapter",
    )
    return (completion.get("content") or "").strip()


async def _premium_llm_json_call(system_prompt: str, user_prompt: str) -> str:
    from services.ai_text import ask_ai_messages

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    completion = await ask_ai_messages(
        _app_settings,
        messages,
        timeout=_OPENROUTER_PREMIUM_TIMEOUT_SEC,
        models=_openrouter_models_for_premium(),
        max_tokens=_PREMIUM_SUMMARY_MAX_TOKENS,
        temperature=_PREMIUM_SUMMARY_TEMPERATURE,
        response_format={"type": "json_object"},
        log_context="hd_premium_fast_facts",
    )
    return (completion.get("content") or "").strip()


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
        log_context="hd_genetic_synthesis",
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
    upgrade_mode: bool = False,
    user_gender: str = "",
    extra_user_instruction: str = "",
) -> dict[str, Any]:
    """
    Один модуль Genetic Synthesis: open_center × anchors × domain → JSON v3.

    LLM вызывается с temperature=0.1; energy_scales — только серверные (read-only).
    """
    _ = upgrade_mode
    scales = _normalize_energy_scales(
        energy_scales if energy_scales is not None else compute_energy_scales_from_math(math_data)
    )
    system_prompt, user_prompt = _build_genetic_synthesis_prompt(
        domain=domain,
        math_data=math_data,
        synthesis_pair=synthesis_pair,
        energy_scales=scales,
        user_gender=user_gender,
        extra_user_instruction=extra_user_instruction,
    )
    errors: list[str] = []
    or_models = _openrouter_models_for_premium()
    or_timeout = _OPENROUTER_PREMIUM_TIMEOUT_SEC

    if _openrouter_configured():
        try:
            async with _hd_llm_semaphore():
                return await _generate_synthesis_via_openrouter(
                    system_prompt,
                    user_prompt,
                    models=or_models,
                    timeout=or_timeout,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "HD genetic synthesis OpenRouter exhausted domain=%s pair=%s — Gemini fallback: %s",
                domain,
                synthesis_pair.get("open_center"),
                exc,
            )
            errors.append(f"openrouter: {exc!r}")
    elif not _gemini_configured():
        raise RuntimeError("hd_synthesis_unavailable: задайте OPENROUTER_API_KEY или GEMINI_API_KEY")

    if _gemini_configured():
        try:
            async with _hd_llm_semaphore():
                return await _generate_synthesis_via_gemini(system_prompt, user_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "HD genetic synthesis Gemini fallback failed domain=%s pair=%s",
                domain,
                synthesis_pair.get("open_center"),
            )
            errors.append(f"gemini: {exc!r}")

    raise RuntimeError("hd_synthesis_unavailable: " + "; ".join(errors))


def _synthesis_rendered_length(block: dict[str, Any]) -> int:
    return len(render_synthesis_block(block))


async def generate_genetic_synthesis_with_retry(
    *,
    domain: str,
    math_data: dict[str, object],
    synthesis_pair: dict[str, object],
    energy_scales: dict[str, int] | None = None,
    user_gender: str = "",
) -> dict[str, Any]:
    """Genetic Synthesis с валидатором минимальной длины и до 2 retry."""
    last_block: dict[str, Any] | None = None
    for attempt in range(_DOMAIN_SYNTHESIS_MAX_RETRIES + 1):
        extra = ""
        if attempt > 0:
            extra = (
                f"ПРЕДЫДУЩИЙ ОТВЕТ СЛИШКОМ КОРОТКИЙ (< {_DOMAIN_CHAPTER_MIN_CHARS} символов). "
                "Расширь client_pain, false_self_pattern и experiments — сохрани JSON-схему."
            )
        block = await generate_genetic_synthesis(
            domain=domain,
            math_data=math_data,
            synthesis_pair=synthesis_pair,
            energy_scales=energy_scales,
            user_gender=user_gender,
            extra_user_instruction=extra,
        )
        last_block = block
        if _synthesis_rendered_length(block) >= _DOMAIN_CHAPTER_MIN_CHARS:
            return block
        logger.warning(
            "HD synthesis domain=%s attempt=%s too short chars=%s",
            domain,
            attempt + 1,
            _synthesis_rendered_length(block),
        )
    if last_block is None:
        raise RuntimeError(f"hd_synthesis_empty domain={domain}")
    return last_block


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
    user_gender: str = "",
    synthesis_excerpt: str = "",
) -> tuple[str, str]:
    """Промпт fast_facts (до 2000 символов) + plan — финальный LLM-pass (вызов 2)."""
    name = (user_name or "").strip() or "друг"
    gender = (user_gender or "").strip()
    hd_type = str(math_data.get("hd_type") or "")
    profile = str(math_data.get("profile") or "")
    authority = str(math_data.get("authority") or "")
    strategy = str(math_data.get("strategy") or "")
    scales_line = _format_energy_scales_line(energy_scales)

    excerpt_parts: list[str] = []
    if synthesis_excerpt.strip():
        excerpt_parts.append(f"[genetic_synthesis]\n{synthesis_excerpt.strip()[:3000]}")
    for domain in ("money", "love", "energy"):
        text = str(domain_excerpts.get(domain) or "").strip()
        if text:
            excerpt_parts.append(f"[{domain}]\n{text[:1800]}")
    excerpts = "\n\n".join(excerpt_parts) or "Контекст глав не передан — опирайся на math_data."

    gender_line = (
        f"Пол клиента (read-only): {gender}."
        if gender
        else "Пол клиента не передан — пиши нейтрально."
    )

    system_prompt = (
        "Ты — ведущий аналитик NeuroMule HD. На основе статики карты и одного блока Genetic Synthesis "
        'сформируй JSON: {"fast_facts": "...", "plan": "..."}\n'
        f"{_HD_PREMIUM_TOV_BLOCK}\n"
        f"- fast_facts: до {_FAST_FACTS_MAX_LEN} символов — сочный «продающий удар» на 2-ю страницу PDF. "
        "Обязателен Эффект Зеркала: кинематографичная бытовая сцена с временем суток "
        "(«Время 22:15, ты сидишь у компа…»). Три якоря в одном поле: "
        "'⚡ Главный баг прошивки: …', '💼 Триггер больших денег: …', '🔋 Идеальная перезагрузка: …'.\n"
        f"- {_HD_BOLD_CHALLENGES_PLAN_RULE}\n"
        "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО: «Неделя 1–4», «осознай», «практикуй», «доверься сигналам».\n"
        f"- {PROFILE_ARCHETYPE_PROMPT_RULE}\n"
        f"- {CHANNEL_ARCHETYPE_PROMPT_RULE}\n"
        "Без символов # в тексте. Только факты карты из user-блока."
    )
    profile_code_line, profile_archetype_line = profile_llm_context_lines(profile)
    user_prompt = (
        f"Клиент: {name}. Тип: {hd_type}.\n"
        f"{gender_line}\n"
        f"{profile_code_line}\n"
        f"{profile_archetype_line}\n"
        f"Авторитет: {authority}. Стратегия: {strategy}.\n"
        f"Шкалы (read-only): {scales_line}\n\n"
        f"Контекст для синтеза:\n{excerpts}\n\n"
        "Сгенерируй fast_facts и plan, согласованные с контекстом."
    )
    return system_prompt, user_prompt


def _normalize_premium_summary(parsed: dict[str, object]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("fast_facts", "plan"):
        value = parsed.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"premium summary missing non-empty {key!r}")
        cleaned = value.strip()
        if text_contains_raw_profile_code(cleaned):
            raise ValueError(f"premium summary field {key!r} contains raw profile code (use archetype)")
        _validate_hd_user_facing_text(cleaned, field=f"premium summary field {key!r}")
        out[key] = cleaned
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

    async with _hd_llm_semaphore():
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
            log_context="hd_premium_summary",
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
    user_gender: str = "",
    synthesis_excerpt: str = "",
) -> dict[str, str]:
    system_prompt, user_prompt = _build_premium_summary_prompt(
        user_name,
        math_data,
        domain_excerpts=domain_excerpts,
        energy_scales=energy_scales,
        user_gender=user_gender,
        synthesis_excerpt=synthesis_excerpt,
    )
    if _openrouter_configured():
        try:
            return await _generate_premium_summary_via_openrouter(
                system_prompt,
                user_prompt,
                models=_openrouter_models_for_premium(),
                timeout=_OPENROUTER_PREMIUM_TIMEOUT_SEC,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenRouter premium summary failed: %s", exc)
    if _gemini_configured():
        try:
            return await _generate_premium_summary_via_gemini(system_prompt, user_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini premium summary fallback failed: %s", exc)
    raise RuntimeError("hd_summary_unavailable")


async def _generate_premium_report_upgrade_fast(
    user_name: str,
    math_data: dict[str, object],
    *,
    user_gender: str = "",
) -> dict[str, Any]:
    """
    Быстрый апгрейд legacy → v3: static IHDS-блоки + один LLM-вызов (без N× synthesis).

    Надёжнее для auto-upgrade на VDS, где multipass не укладывается в короткий таймаут.
    """
    from services.hd_static_blocks import assemble_static_reference, format_static_reference_full

    energy_scales = compute_energy_scales_from_math(math_data)
    static_sections = assemble_static_reference(math_data, gate_to_center=_GATE_TO_CENTER)
    static_full = format_static_reference_full(static_sections)

    system_prompt, user_prompt = _build_elite_premium_hd_prompt(
        user_name,
        math_data,
        user_gender=user_gender,
    )
    or_models = _openrouter_models_for_premium_upgrade()
    or_timeout = _OPENROUTER_PREMIUM_UPGRADE_TIMEOUT_SEC

    report: dict[str, Any] | None = None
    if _openrouter_configured():
        try:
            report = await _generate_premium_via_openrouter(
                system_prompt,
                user_prompt,
                models=or_models,
                timeout=or_timeout,
                relax_cliches=True,
                active_channels=math_data.get("active_channels"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("upgrade-fast OpenRouter failed: %s", exc)

    if report is None and _gemini_configured():
        try:
            report = await _generate_premium_via_gemini(
                system_prompt,
                user_prompt,
                relax_cliches=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("upgrade-fast Gemini failed: %s", exc)

    if report is None:
        raise RuntimeError("upgrade_fast_llm_unavailable")

    report["energy_scales"] = energy_scales
    report["static_reference"] = static_sections
    report["synthesis_meta"] = {
        "pairs_requested": 0,
        "blocks_ok": 0,
        "blocks_failed": 0,
        "static_pages_est": max(1, len(static_full) // 2200),
        "upgrade_fast": True,
    }
    return report


async def _synthesize_pair_domains_parallel(
    pair: dict[str, object],
    *,
    math_data: dict[str, object],
    energy_scales: dict[str, int],
    upgrade_mode: bool,
) -> tuple[list[tuple[str, dict[str, Any]]], int]:
    """Параллельный synthesis money/love/energy для одной open×motor пары."""

    async def _one(domain: str) -> tuple[str, dict[str, Any] | BaseException]:
        try:
            block = await generate_genetic_synthesis(
                domain=domain,
                math_data=math_data,
                synthesis_pair=pair,
                energy_scales=energy_scales,
                upgrade_mode=upgrade_mode,
            )
            block["_pair"] = {
                "open_center": pair.get("open_center"),
                "anchors": pair.get("anchors"),
            }
            return domain, block
        except Exception as exc:  # noqa: BLE001
            return domain, exc

    outcomes = await asyncio.gather(
        *[_one(domain) for domain in sorted(_GENETIC_SYNTHESIS_DOMAINS)]
    )
    ok: list[tuple[str, dict[str, Any]]] = []
    failed = 0
    for domain, result in outcomes:
        if isinstance(result, BaseException):
            failed += 1
            logger.warning(
                "genetic synthesis failed domain=%s open=%s: %s",
                domain,
                pair.get("open_center"),
                result,
            )
            continue
        ok.append((domain, result))
    return ok, failed


async def _generate_premium_report_multipass(
    user_name: str,
    math_data: dict[str, object],
    *,
    user_gender: str = "",
) -> dict[str, Any]:
    """
    Quiet Luxury premium HD: parallel markdown-главы + fast_facts + static blocks.
    """
    from services.hd_premium_chapters import generate_premium_report_quiet_luxury
    from services.hd_static_blocks import assemble_static_reference, format_static_reference_full

    energy_scales = compute_energy_scales_from_math(math_data)
    static_sections = assemble_static_reference(math_data, gate_to_center=_GATE_TO_CENTER)
    static_full = format_static_reference_full(static_sections)

    if not _openrouter_configured() and not _gemini_configured():
        raise RuntimeError("hd_premium_llm_unavailable")

    async def _markdown_wrapped(system_prompt: str, user_prompt: str) -> str:
        async with _hd_llm_semaphore():
            if _openrouter_configured():
                try:
                    return await _premium_llm_markdown_call(system_prompt, user_prompt)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("premium chapter OpenRouter failed: %s", exc)
            if _gemini_configured():
                client = _configure_genai()
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=_GEMINI_PREMIUM_MODEL_CHAIN[0],
                        contents=user_prompt,
                        config={
                            "max_output_tokens": _GENETIC_SYNTHESIS_MAX_TOKENS,
                            "system_instruction": system_prompt,
                            "temperature": 0.15,
                        },
                    ),
                    timeout=_GEMINI_PREMIUM_TIMEOUT_SEC,
                )
                return _extract_gemini_text(response).strip()
            raise RuntimeError("hd_premium_llm_unavailable")

    async def _json_wrapped(system_prompt: str, user_prompt: str) -> str:
        async with _hd_llm_semaphore():
            if _openrouter_configured():
                return await _premium_llm_json_call(system_prompt, user_prompt)
            client = _configure_genai()
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=_GEMINI_PREMIUM_MODEL_CHAIN[0],
                    contents=user_prompt,
                    config={
                        "response_mime_type": "application/json",
                        "max_output_tokens": _PREMIUM_SUMMARY_MAX_TOKENS,
                        "system_instruction": system_prompt,
                        "temperature": _PREMIUM_SUMMARY_TEMPERATURE,
                    },
                ),
                timeout=_GEMINI_PREMIUM_TIMEOUT_SEC,
            )
            return _extract_gemini_text(response)

    report = await generate_premium_report_quiet_luxury(
        user_name,
        math_data,
        user_gender=user_gender,
        llm_markdown_call=_markdown_wrapped,
        llm_json_call=_json_wrapped,
        energy_scales=energy_scales,
        static_sections=static_sections,
    )
    meta = report.get("synthesis_meta")
    if isinstance(meta, dict):
        meta["static_pages_est"] = max(1, len(static_full) // 2200)
        meta["llm_calls"] = meta.get("chapters_ok", 0) + 1
    logger.info("HD premium report served via Quiet Luxury multipass")
    return report


async def _generate_premium_report_multipass_legacy(
    user_name: str,
    math_data: dict[str, object],
    *,
    user_gender: str = "",
) -> dict[str, Any]:
    """Legacy Genetic Synthesis multipass (fallback)."""
    from services.hd_static_blocks import (
        assemble_static_reference,
        format_static_reference_for_domain,
        format_static_reference_full,
    )

    energy_scales = compute_energy_scales_from_math(math_data)
    static_sections = assemble_static_reference(math_data, gate_to_center=_GATE_TO_CENTER)
    static_full = format_static_reference_full(static_sections)

    domain_pairs_raw = math_data.get("domain_synthesis_pairs")
    if not isinstance(domain_pairs_raw, dict):
        domain_pairs_raw = hd_chart.build_domain_synthesis_pairs(math_data)

    async def _synthesize_one(domain: str) -> tuple[str, dict[str, Any] | None]:
        pair = domain_pairs_raw.get(domain)
        if not isinstance(pair, dict):
            return domain, None
        try:
            block = await generate_genetic_synthesis_with_retry(
                domain=domain,
                math_data=math_data,
                synthesis_pair=pair,
                energy_scales=energy_scales,
                user_gender=user_gender,
            )
            block["_pair"] = {
                "open_center": pair.get("open_center"),
                "anchors": pair.get("anchors"),
            }
            return domain, block
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "genetic synthesis failed domain=%s open=%s: %s",
                domain,
                pair.get("open_center"),
                exc,
            )
            return domain, None

    parallel_results = await asyncio.gather(
        _synthesize_one("money"),
        _synthesize_one("love"),
        _synthesize_one("energy"),
    )
    synthesis_by_domain: dict[str, list[dict[str, Any]]] = {
        "money": [],
        "love": [],
        "energy": [],
    }
    failed_pairs = 0
    for domain, block in parallel_results:
        if block is None:
            failed_pairs += 1
        else:
            synthesis_by_domain[domain].append(block)

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
        user_gender=user_gender,
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
            "pairs_requested": 3,
            "blocks_ok": successful,
            "blocks_failed": failed_pairs,
            "llm_calls": successful + 1,
            "static_pages_est": max(1, len(static_full) // 2200),
            "domain_pairs": True,
            "hybrid_static": True,
            "parallel_domains": True,
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
        "none",
    }:
        return ""
    return text


def _pdf_bold_font_name(font_name: str) -> str:
    if pdfmetrics is not None and _PDF_FONT_BOLD_NAME in pdfmetrics.getRegisteredFontNames():
        return _PDF_FONT_BOLD_NAME
    return font_name


def _strip_chapter_static_preamble(body: str) -> str:
    """Убирает дублирующую статику из LLM-глав — она уже есть в static-секциях PDF."""
    text = str(body or "").strip()
    if "Статическая база карты:" not in text:
        return text
    for marker in ("**Якорь синтеза**", "Синтез 1:", "Синтез 1"):
        idx = text.find(marker)
        if idx > 0:
            return text[idx:].strip()
    return text


def _split_static_blocks(body: str, *, marker_prefix: str) -> list[str]:
    text = str(body or "").strip()
    if not text:
        return []
    parts = re.split(rf"(?=^{re.escape(marker_prefix)})", text, flags=re.MULTILINE)
    return [part.strip() for part in parts if part.strip()]


def _md_to_reportlab_html(text: object) -> str:
    """Конвертирует ограниченный markdown (**жирный**, переносы) в HTML для Paragraph."""
    raw = _sanitize_pdf_plain_text(text)
    if not raw:
        return ""
    chunks: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            chunks.append("<br/>")
            continue
        if stripped.startswith("- "):
            chunks.append(f"• {html_module.escape(stripped[2:])}")
            continue
        if re.fullmatch(r"\*\*.+\*\*", stripped):
            inner = html_module.escape(stripped[2:-2].strip())
            chunks.append(f"<b><font color='#6D28D9' size='12'>{inner}</font></b>")
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


class _HdCalloutBoxFlowable(_PdfFlowableBase):
    """Карточка с фиолетовой рамкой для инсайтов, центров и экспресс-анализа."""

    def __init__(
        self,
        html_text: str,
        *,
        width: float = 480,
        body_style: Any,
        fill_hex: str = "#F5F3FF",
    ) -> None:
        super().__init__()
        self.width = width
        self.pad = 14
        self.paragraph = Paragraph(html_text, body_style)
        _w, h = self.paragraph.wrap(max(10.0, width - 2 * self.pad), 10000)
        self.height = h + 2 * self.pad + 6
        self.fill_hex = fill_hex

    def draw(self) -> None:
        if colors is None:
            return
        self.canv.setFillColor(colors.HexColor(self.fill_hex))
        self.canv.setStrokeColor(colors.HexColor(_PDF_ACCENT_HEX))
        self.canv.setLineWidth(0.8)
        self.canv.roundRect(0, 0, self.width, self.height, 10, fill=1, stroke=1)
        self.paragraph.drawOn(self.canv, self.pad, self.pad + 2)


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
        hd_type: str = "",
    ) -> None:
        if BaseDocTemplate is None or Frame is None or PageTemplate is None or A4 is None:
            raise RuntimeError("Установите пакет reportlab для PDF-отчетов.")
        self.hd_user_name = (user_name or "").strip() or "друг"
        self.hd_birth_data = (birth_data or "").strip()
        self.hd_font_name = font_name
        self.hd_type = (hd_type or "").strip()
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
        try:
            canv.setFillColor(colors.HexColor("#8B5CF6"))
            canv.setFillAlpha(0.07)
            canv.circle(w * 0.18, h * 0.78, 130, fill=1, stroke=0)
            canv.circle(w * 0.82, h * 0.22, 160, fill=1, stroke=0)
            canv.circle(w * 0.55, h * 0.55, 90, fill=1, stroke=0)
            canv.setFillAlpha(1)
        except Exception:
            pass
        name = _sanitize_pdf_display_name(self.hd_user_name)
        canv.setFillColor(colors.HexColor(_HD_NEON_HEX))
        canv.setFont(self.hd_font_name, 24)
        canv.drawCentredString(w / 2, h * 0.64, "NEUROMULE HD PREMIUM")
        canv.setFont(self.hd_font_name, 15)
        canv.drawCentredString(w / 2, h * 0.58, "ПЕРСОНАЛЬНЫЙ НАВИГАТОР ЛИЧНОСТИ")
        canv.setFillColor(colors.HexColor("#E8E8F0"))
        canv.setFont(self.hd_font_name, 14)
        canv.drawCentredString(w / 2, h * 0.50, name[:72])
        if self.hd_type:
            canv.setFillColor(colors.HexColor("#C4B5FD"))
            canv.setFont(self.hd_font_name, 12)
            canv.drawCentredString(w / 2, h * 0.44, self.hd_type[:64])
        if self.hd_birth_data:
            canv.setFillColor(colors.HexColor("#C8C8D8"))
            canv.setFont(self.hd_font_name, 11)
            canv.drawCentredString(w / 2, h * 0.38, self.hd_birth_data[:90])
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


_PDF_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]+", flags=re.UNICODE)


def _sanitize_pdf_plain_text(text: object) -> str:
    cleaned = _PDF_EMOJI_RE.sub("", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip()


def _sanitize_report_for_pdf(report: dict[str, Any]) -> dict[str, Any]:
    """Убирает эмодзи и управляющие символы — ReportLab/Roboto на Linux падают на emoji."""
    sanitized: dict[str, Any] = dict(report)
    for key in (*_PREMIUM_REPORT_KEYS, "fast_facts"):
        val = sanitized.get(key)
        if isinstance(val, str):
            sanitized[key] = _sanitize_pdf_plain_text(val)
    static_raw = sanitized.get("static_reference")
    if isinstance(static_raw, dict):
        sanitized["static_reference"] = {
            k: _sanitize_pdf_plain_text(v) if isinstance(v, str) else v
            for k, v in static_raw.items()
        }
    return sanitized


def _sanitize_pdf_display_name(name: str) -> str:
    """Убирает эмодзи и мусорные AI-ники из Telegram display_name."""
    ai_blocklist = re.compile(
        r"(chatgpt|sora|suno|deepseek|gpt|openai|claude|gemini)",
        re.IGNORECASE,
    )
    text = _PDF_EMOJI_RE.sub("", (name or "").strip())
    text = re.sub(r"\s+", " ", text).strip()
    if not text or ai_blocklist.search(text):
        return "Клиент NeuroMule"
    return text[:64].rstrip()


def _build_key_activations_table(
    math_data: dict[str, object],
    font_name: str,
) -> Table:
    activations = math_data.get("key_activations")
    rows: list[list[str]] = [["Активация", "Ворота", "Линия", "Центр"]]
    labels = (
        ("personality_sun", "Солнце Личности"),
        ("personality_earth", "Земля Личности"),
        ("design_sun", "Солнце Дизайна"),
        ("design_earth", "Земля Дизайна"),
    )
    if isinstance(activations, dict):
        for key, label in labels:
            payload = activations.get(key)
            if not isinstance(payload, dict):
                continue
            gate = payload.get("gate")
            line = payload.get("line")
            center = _GATE_TO_CENTER.get(int(gate), "—") if isinstance(gate, int) else "—"
            rows.append([label, str(gate or "—"), str(line or "—"), center])
    table = Table(rows, colWidths=[150, 70, 60, 200])
    if colors is not None and TableStyle is not None:
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDE9FE")),
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D4D4DC")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E8E8EE")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
    return table


def _build_gates_appendix_table(
    math_data: dict[str, object],
    font_name: str,
) -> Table:
    gates = math_data.get("gates")
    rows: list[list[str]] = [["Планета", "Ворота", "Линия", "Центр"]]
    if isinstance(gates, dict):
        for planet, payload in sorted(gates.items(), key=lambda item: str(item[0])):
            if not isinstance(payload, dict):
                continue
            gate = payload.get("gate")
            line = payload.get("line")
            if gate is None:
                continue
            center = _GATE_TO_CENTER.get(int(gate), "—") if isinstance(gate, int) else "—"
            rows.append([str(planet), str(gate), str(line or "—"), center])
    table = Table(rows, colWidths=[130, 60, 50, 240])
    if colors is not None and TableStyle is not None and len(rows) > 1:
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#D4D4DC")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.2, colors.HexColor("#E8E8EE")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
    return table


def _build_chart_overview_table(
    meta: dict[str, object],
    font_name: str,
) -> Table:
    rows: list[list[str]] = []
    for label, raw in (
        ("Истинный Тип", meta.get("hd_type")),
        ("Профиль", meta.get("profile_archetype") or format_profile_archetype_for_user(str(meta.get("profile") or ""))),
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
    math_data: dict[str, object],
    font_name: str,
    include_appendix: bool = True,
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

    bold_font = _pdf_bold_font_name(font_name)
    title_style = ParagraphStyle(
        "HdChapterTitle",
        fontName=bold_font,
        fontSize=17,
        leading=21,
        textColor=colors.HexColor("#1A1A24") if colors else None,
        spaceAfter=4,
    )
    overview_style = ParagraphStyle(
        "HdOverviewTitle",
        fontName=bold_font,
        fontSize=20,
        leading=24,
        textColor=colors.HexColor(_HD_NEON_HEX) if colors else None,
        spaceAfter=10,
    )
    body_style = ParagraphStyle(
        "HdBody",
        fontName=font_name,
        fontSize=12,
        leading=18,
        textColor=colors.HexColor("#1A1A24") if colors else None,
        spaceAfter=8,
    )
    callout_style = ParagraphStyle(
        "HdCallout",
        fontName=font_name,
        fontSize=10.5,
        leading=15,
        textColor=colors.HexColor("#1A1A24") if colors else None,
        spaceAfter=4,
    )

    story: list[Any] = [Spacer(1, 1)]
    story.append(NextPageTemplate("Content"))
    story.append(PageBreak())

    fast_facts = str(report.get("fast_facts") or "").strip()
    if fast_facts:
        story.append(_HdPdfBookmark("Экспресс-анализ", "hd_fast_facts"))
        story.append(Paragraph("Экспресс-анализ", overview_style))
        story.append(_HdAccentBarFlowable(width=480))
        story.append(Spacer(1, 10))
        for line in fast_facts.splitlines():
            chunk = line.strip()
            if not chunk:
                continue
            html = _md_to_reportlab_html(chunk)
            if html:
                story.append(_HdCalloutBoxFlowable(html, body_style=callout_style))
                story.append(Spacer(1, 6))
        story.append(PageBreak())

    story.append(_HdPdfBookmark("Обзор карты", "hd_overview"))
    story.append(Paragraph("Обзор карты", overview_style))
    story.append(_HdAccentBarFlowable(width=480))
    story.append(Spacer(1, 10))
    story.append(_build_chart_overview_table(meta, font_name))
    story.append(Spacer(1, 16))

    bg_path = _prepare_bodygraph_for_pdf(user_id, birth_data)
    if bg_path:
        try:
            display_w = _PDF_BODYGRAPH_WIDTH_PX * 0.72
            img = RLImage(bg_path, width=display_w, height=display_w, kind="proportional")
            img.hAlign = "CENTER"
            story.append(img)
            story.append(Spacer(1, 12))
        except Exception:
            logger.warning("hd pdf bodygraph image skipped uid=%s", user_id, exc_info=True)

    scales = report.get("energy_scales")
    if isinstance(scales, dict):
        story.append(Paragraph("<b>Шкалы энергии</b>", body_style))
        story.append(_HdEnergyScalesFlowable(scales, font_name=font_name))
    story.append(PageBreak())

    from services.hd_premium_prompts import PREMIUM_PDF_CHAPTER_SPECS

    chapter_blocks: list[tuple[str, str, str, str]] = []
    for key, chapter_title, bookmark_key in PREMIUM_PDF_CHAPTER_SPECS:
        body = report.get(key)
        if body and str(body).strip():
            chapter_blocks.append((key, chapter_title, bookmark_key, str(body)))

    for idx, (_key, chapter_title, bookmark_key, body) in enumerate(chapter_blocks):
        story.append(_HdPdfBookmark(chapter_title, bookmark_key))
        story.append(Paragraph(html_module.escape(chapter_title), title_style))
        story.append(_HdAccentBarFlowable(width=480))
        story.append(Spacer(1, 10))
        chapter_text = _strip_chapter_static_preamble(body)
        _append_pdf_markdown_paragraphs(story, chapter_text, body_style, line_spacer=8)
        if idx < len(chapter_blocks) - 1:
            story.append(PageBreak())

    if include_appendix:
        story.append(PageBreak())
        story.append(_HdPdfBookmark("Приложение", "hd_appendix"))
        story.append(Paragraph("Полная таблица ворот (компактно)", title_style))
        story.append(Spacer(1, 8))
        story.append(_build_gates_appendix_table(math_data, font_name))

    return story


def _append_pdf_markdown_paragraphs(
    story: list[Any],
    text: str,
    body_style: Any,
    *,
    line_spacer: int = 6,
) -> None:
    """Безопасный вывод markdown-текста через Paragraph (без callout-box)."""
    if Paragraph is None or Spacer is None:
        return
    for line in (text or "").splitlines():
        chunk = line.strip()
        if not chunk:
            continue
        html = _md_to_reportlab_html(chunk)
        if html:
            story.append(Paragraph(html, body_style))
            story.append(Spacer(1, line_spacer))


def _build_hd_minimal_pdf_story(
    report: dict[str, Any],
    *,
    meta: dict[str, object],
    font_name: str,
    user_id: int = 0,
    math_data: dict[str, object] | None = None,
    birth_data: str | None = None,
) -> list[Any]:
    """Резервный PDF: все секции через Paragraph (без callout-box), если полная сборка упала."""
    if Paragraph is None or Spacer is None or PageBreak is None or ParagraphStyle is None:
        raise RuntimeError("Установите пакет reportlab для PDF-отчетов.")
    bold_font = _pdf_bold_font_name(font_name)
    body_style = ParagraphStyle(
        "HdMinimalBody",
        fontName=font_name,
        fontSize=12,
        leading=18,
        textColor=colors.HexColor("#1A1A24") if colors else None,
        spaceAfter=8,
    )
    title_style = ParagraphStyle(
        "HdMinimalTitle",
        fontName=bold_font,
        fontSize=16,
        leading=22,
        textColor=colors.HexColor("#1A1A24") if colors else None,
        spaceAfter=8,
    )
    overview_style = ParagraphStyle(
        "HdMinimalOverview",
        fontName=bold_font,
        fontSize=18,
        leading=22,
        textColor=colors.HexColor(_HD_NEON_HEX) if colors else None,
        spaceAfter=10,
    )
    story: list[Any] = [Spacer(1, 1), PageBreak()]

    fast_facts = str(report.get("fast_facts") or "").strip()
    if fast_facts:
        story.append(Paragraph("Экспресс-анализ", overview_style))
        _append_pdf_markdown_paragraphs(story, fast_facts, body_style)
        story.append(PageBreak())

    story.append(Paragraph("Обзор карты", overview_style))
    story.append(_build_chart_overview_table(meta, font_name))
    story.append(Spacer(1, 16))

    if user_id and RLImage is not None:
        bg_path = _prepare_bodygraph_for_pdf(user_id, birth_data)
        if bg_path:
            try:
                display_w = _PDF_BODYGRAPH_WIDTH_PX * 0.72
                img = RLImage(bg_path, width=display_w, height=display_w, kind="proportional")
                img.hAlign = "CENTER"
                story.append(img)
                story.append(Spacer(1, 12))
            except Exception:
                logger.warning("hd minimal pdf bodygraph skipped uid=%s", user_id, exc_info=True)

    scales = report.get("energy_scales")
    if isinstance(scales, dict):
        story.append(Paragraph("<b>Шкалы энергии</b>", body_style))
        story.append(_HdEnergyScalesFlowable(scales, font_name=font_name))
    story.append(PageBreak())

    if math_data is not None:
        _ = _resolve_static_sections_for_pdf(report, math_data)

    from services.hd_premium_prompts import PREMIUM_PDF_CHAPTER_SPECS

    for key, title, _bookmark in PREMIUM_PDF_CHAPTER_SPECS:
        body = str(report.get(key) or "").strip()
        if not body:
            continue
        story.append(Paragraph(html_module.escape(title), title_style))
        chapter_text = _strip_chapter_static_preamble(body)
        _append_pdf_markdown_paragraphs(story, chapter_text, body_style, line_spacer=8)
        story.append(Spacer(1, 12))
    return story


def _write_hd_premium_pdf_file(
    user_id: int,
    report: dict[str, Any],
    birth_data: str | None,
    *,
    hd_type: str = "",
    user_name: str = "",
    minimal: bool = False,
) -> str:
    if BaseDocTemplate is None or A4 is None:
        raise RuntimeError("Установите пакет reportlab для PDF-отчетов.")
    ensure_pdf_fonts_available()
    math_data = build_hd_math_data(hd_type or "не указан", birth_data or "")
    meta = hd_profile_metadata(math_data)
    report_for_pdf = _sanitize_report_for_pdf(report)
    static_raw = report_for_pdf.get("static_reference")
    if not isinstance(static_raw, dict) or not static_raw:
        from services.hd_static_blocks import assemble_static_reference

        report_for_pdf["static_reference"] = assemble_static_reference(
            math_data,
            gate_to_center=_GATE_TO_CENTER,
        )
    os.makedirs(str(_HD_BODYGRAPH_OUTPUT_DIR), exist_ok=True)
    path = _HD_BODYGRAPH_OUTPUT_DIR / f"report_{user_id}.pdf"
    font_name = _register_pdf_font()
    doc = _HdPremiumPdfDoc(
        str(path),
        user_name=_sanitize_pdf_display_name(user_name),
        birth_data=str(meta.get("birth_data") or birth_data or ""),
        font_name=font_name,
        hd_type=str(meta.get("hd_type") or hd_type or ""),
    )
    minimal_kwargs = {
        "meta": meta,
        "font_name": font_name,
        "user_id": user_id,
        "math_data": math_data,
        "birth_data": birth_data,
    }
    if minimal:
        doc.build(_build_hd_minimal_pdf_story(report_for_pdf, **minimal_kwargs))
    else:
        built = False
        for include_appendix in (True, False):
            try:
                story = _build_hd_premium_pdf_story(
                    user_id,
                    report_for_pdf,
                    user_name=user_name,
                    birth_data=birth_data,
                    meta=meta,
                    math_data=math_data,
                    font_name=font_name,
                    include_appendix=include_appendix,
                )
                doc.build(story)
                built = True
                break
            except Exception:
                phase = "full" if include_appendix else "without appendix"
                logger.exception(
                    "HD premium PDF %s build failed uid=%s",
                    phase,
                    user_id,
                )
        if not built:
            doc.build(_build_hd_minimal_pdf_story(report_for_pdf, **minimal_kwargs))
    if not path.is_file() or path.stat().st_size < 512:
        raise RuntimeError(f"HD PDF empty after build path={path}")
    return str(path)


def create_hd_premium_pdf(
    user_id: int,
    report: dict[str, Any],
    birth_data: str | None,
    *,
    hd_type: str = "",
    user_name: str = "",
) -> str:
    """Премиальный PDF: обложка, Chart Overview, energy scales, главы с закладками."""
    return _write_hd_premium_pdf_file(
        user_id,
        report,
        birth_data,
        hd_type=hd_type,
        user_name=user_name,
        minimal=False,
    )


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
_STORY_JPG_QUALITY = 98
_STORY_FONT_DIR = _PROJECT_ROOT / "assets" / "fonts"
_STORY_FONT_BOLD_PATH = _STORY_FONT_DIR / "Roboto-Bold.ttf"
_STORY_FONT_REGULAR_PATH = _STORY_FONT_DIR / "Roboto-Regular.ttf"
_STORY_COLOR_BG = (14, 10, 26, 255)
_STORY_COLOR_WHITE = (255, 255, 255, 255)
_STORY_COLOR_LAVENDER = (196, 188, 224, 255)
_STORY_COLOR_LAVENDER_BRIGHT = (215, 208, 240, 255)
_STORY_COLOR_GRAY = (130, 128, 142, 255)
_STORY_COLOR_LINE = (255, 255, 255, 25)
_STORY_COLOR_TRIGGER_LINE = (255, 255, 255, 51)
_STORY_COLOR_FOOTER_RGB = (110, 105, 125)
_STORY_COLOR_WHITE_RGB = (255, 255, 255)
_STORY_COLOR_LABEL_RGB = (210, 200, 240)
_STORY_COLOR_WATERMARK_RGB = (70, 67, 80)
_STORY_PANEL_INNER_WIDTH = 760
_STORY_MARGIN_X = 80
_STORY_PARAM_RIGHT_COL_X = 620
_STORY_LINE_SPACING = 44
_STORY_CONTENT_W = 920
_STORY_PANEL_PAD_X = 40
_STORY_WRAP_MAX_CHARS = 43
_STORY_CHANNEL_COPY_OVERRIDES: tuple[tuple[str, str, str], ...] = (
    (
        "сакральной самонаправленности",
        "Абсолютная верность своему пути. Деньги приходят, когда делаешь то, от чего кайфуешь сам.",
        "Пахать на чужие цели и пытаться угодить другим.",
    ),
    (
        "эмоциональной выразительности",
        "Сумасшедший магнетизм и глубина. Ты влюбляешь в себя людей, просто транслируя свои настоящие чувства.",
        "Накручивать драму на пустом месте ради чужого внимания.",
    ),
    (
        "стойкости в борьбе за смысл",
        "Невероятное упрямство и азарт. Способность преодолеть любой кризис, если видишь в этом большой смысл.",
        "Сливать силы на пустые споры и бессмысленную борьбу.",
    ),
)


def ensure_story_fonts_available() -> None:
    """Pre-flight: кириллические TTF для Instagram Stories должны лежать в assets/fonts/."""
    missing = [
        path for path in (_STORY_FONT_BOLD_PATH, _STORY_FONT_REGULAR_PATH) if not path.is_file()
    ]
    if missing:
        raise RuntimeError(
            "HD story fonts missing on disk: " + ", ".join(str(p) for p in missing)
        )


def _load_story_font(size: int, *, bold: bool = False) -> Any:
    """TTF с кириллицей для Instagram Stories (без load_default fallback)."""
    if ImageFont is None:
        return None
    primary = _STORY_FONT_BOLD_PATH if bold else _STORY_FONT_REGULAR_PATH
    secondary = _STORY_FONT_REGULAR_PATH if bold else _STORY_FONT_BOLD_PATH
    for candidate in (
        primary,
        secondary,
        _PROJECT_ROOT / "fonts" / "Roboto-Regular.ttf",
    ):
        if not candidate.is_file():
            continue
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except OSError:
            logger.warning("story font load failed path=%s", candidate, exc_info=True)
    raise RuntimeError(
        "Cyrillic story fonts missing: expected "
        f"{_STORY_FONT_BOLD_PATH} and {_STORY_FONT_REGULAR_PATH}"
    )


def _create_story_minimal_background() -> Any:
    """Чистый тёмный фон + одна центральная фиолетово-синяя туманность."""
    if Image is None or ImageDraw is None or ImageFilter is None:
        raise RuntimeError("Pillow required")
    size = _STORY_CANVAS_SIZE
    w, h = size
    bg = Image.new("RGBA", size, _STORY_COLOR_BG)
    glow_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    gl_draw = ImageDraw.Draw(glow_layer)
    cx, cy = w // 2, h // 2
    gl_draw.ellipse(
        (cx - 520, cy - 620, cx + 520, cy + 620),
        fill=(48, 36, 120, 89),
    )
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(150))
    return Image.alpha_composite(bg, glow_layer)


def _story_rgba_fill_on_dark(
    r: int,
    g: int,
    b: int,
    a: int,
    *,
    bg: tuple[int, int, int] = (14, 10, 26),
) -> tuple[int, int, int]:
    """Приближение RGBA-заливки на тёмном фоне для RGB-холста."""
    t = max(0, min(255, a)) / 255.0
    return (
        int(r * t + bg[0] * (1.0 - t)),
        int(g * t + bg[1] * (1.0 - t)),
        int(b * t + bg[2] * (1.0 - t)),
    )


def _draw_story_glass_panel(
    base_img: Any,
    coords: tuple[int, int, int, int],
    *,
    radius: int = 24,
) -> Any:
    """Матовое стекло: плотная светлая заливка для контраста текста."""
    overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    draw_overlay.rounded_rectangle(coords, radius=radius, fill=(255, 255, 255, 45))
    draw_overlay.rounded_rectangle(coords, radius=radius, outline=(255, 255, 255, 60), width=1)
    return Image.alpha_composite(base_img, overlay)


def draw_premium_glass_panel(
    base_img: Any,
    coords: tuple[int, int, int, int],
    *,
    radius: int = 24,
) -> Any:
    """Публичный alias для premium glass panel в Stories."""
    return _draw_story_glass_panel(base_img, coords, radius=radius)


def _draw_story_panel(
    base_img: Any,
    coords: tuple[int, int, int, int],
    *,
    radius: int = 16,
) -> Any:
    return _draw_story_glass_panel(base_img, coords, radius=radius)


def _extract_bodygraph_layers(bodygraph_path: Path) -> tuple[Any | None, Any | None]:
    if Image is None or ImageFilter is None or not bodygraph_path.is_file():
        return None, None
    img = Image.open(bodygraph_path).convert("RGBA")
    gray = img.convert("L")
    clean_body = Image.merge(
        "RGBA",
        (
            Image.new("L", img.size, 255),
            Image.new("L", img.size, 255),
            Image.new("L", img.size, 255),
            gray,
        ),
    )
    glow = Image.merge(
        "RGBA",
        (
            Image.new("L", img.size, 140),
            Image.new("L", img.size, 80),
            Image.new("L", img.size, 255),
            gray,
        ),
    ).filter(ImageFilter.GaussianBlur(35))
    return clean_body, glow


def _draw_story_multiline_text(
    draw_obj: Any,
    text: str,
    position: tuple[int, int],
    font: Any,
    *,
    max_width: int = _STORY_PANEL_INNER_WIDTH,
    fill: tuple[int, ...] = _STORY_COLOR_WHITE_RGB,
    line_spacing: int = _STORY_LINE_SPACING,
    max_lines: int = 2,
) -> int:
    """Перенос по ширине в пикселях; без обрезки троеточием (только на RGB-холсте)."""
    words = (text or "").split()
    if not words:
        return position[1]
    lines: list[str] = []
    current = ""
    word_idx = 0
    while word_idx < len(words) and len(lines) < max_lines:
        word = words[word_idx]
        candidate = f"{current} {word}".strip() if current else word
        bbox = draw_obj.textbbox((0, 0), candidate, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            current = candidate
            word_idx += 1
            continue
        if current:
            lines.append(current)
            current = ""
            continue
        lines.append(word)
        word_idx += 1
    if current and len(lines) < max_lines:
        lines.append(current)
    x, y = position
    for line in lines[:max_lines]:
        draw_obj.text((x, y), line, fill=fill, font=font)
        y += line_spacing
    return y


def draw_multiline_text(
    draw_obj: Any,
    text: str,
    position: tuple[int, int],
    font: Any,
    **kwargs: Any,
) -> int:
    """Публичный alias для переноса текста в Stories."""
    return _draw_story_multiline_text(draw_obj, text, position, font, **kwargs)


def _story_footer_label() -> str:
    return "//  NEUROMULE_BOT  //"


def _save_story_jpg(card: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    card.convert("RGB").save(path, format="JPEG", quality=_STORY_JPG_QUALITY, optimize=True)


def _create_story_premium_background(size: tuple[int, int] = _STORY_CANVAS_SIZE) -> Any:
    """Backward-compatible alias для тестов."""
    _ = size
    return _create_story_minimal_background()


def _load_story_bodygraph_neon(bodygraph_path: Path) -> Any | None:
    """Светлые линии бодиграфа на прозрачном фоне."""
    if Image is None or not bodygraph_path.is_file():
        return None
    img = Image.open(bodygraph_path).convert("RGBA")
    gray = img.convert("L")
    white = Image.new("L", img.size, 255)
    return Image.merge("RGBA", (white, white, white, gray))


def _create_story_bodygraph_glow(source_img: Any, *, radius: int = 38) -> Any:
    """Мягкое белое свечение вокруг бодиграфа."""
    if Image is None or ImageFilter is None:
        raise RuntimeError("Pillow required")
    _r, _g, _b, alpha = source_img.split()
    soft_alpha = alpha.point(lambda p: min(255, int(p * 0.28)))
    glow = Image.merge(
        "RGBA",
        (
            Image.new("L", source_img.size, 255),
            Image.new("L", source_img.size, 255),
            Image.new("L", source_img.size, 255),
            soft_alpha,
        ),
    )
    return glow.filter(ImageFilter.GaussianBlur(radius))


def _story_wrap_words(text: str, max_len: int) -> tuple[str, str]:
    words = (text or "").split()
    line1, line2 = "", ""
    for word in words:
        candidate = f"{line1} {word}".strip() if line1 else word
        if len(candidate) <= max_len:
            line1 = candidate
        else:
            line2 = f"{line2} {word}".strip() if line2 else word
    return line1, line2


def _story_wrap_lines(text: str, max_len: int, *, max_lines: int = 2) -> list[str]:
    """Word-wrap для Stories: до max_lines строк по max_len символов."""
    words = (text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    idx = 0
    while idx < len(words) and len(lines) < max_lines:
        word = words[idx]
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= max_len:
            current = candidate
            idx += 1
            continue
        if current:
            lines.append(current)
            current = ""
            continue
        lines.append(word[: max_len - 1] + "…")
        idx += 1
    if current and len(lines) < max_lines:
        lines.append(current)
    if idx < len(words) and lines:
        tail = lines[-1]
        if not tail.endswith("…"):
            lines[-1] = (tail[: max_len - 1].rstrip() + "…") if len(tail) >= max_len else tail + "…"
    return lines or [""]


def _story_humanize_channel_copy(superpower: str, trigger: str) -> tuple[str, str]:
    """Заменяет заумные HD-термины на понятный язык для Stories."""
    needle = superpower.lower()
    for marker, plain_text, plain_trigger in _STORY_CHANNEL_COPY_OVERRIDES:
        if marker in needle:
            return plain_text, plain_trigger
    cleaned = (superpower or "").strip()
    lowered = cleaned.lower()
    if lowered.startswith("суперсила:"):
        cleaned = cleaned.split(":", 1)[1].strip()
    elif lowered.startswith("суперсила "):
        cleaned = cleaned[len("Суперсила ") :].strip()
    return cleaned or superpower, trigger


def _story_draw_hrule(draw: Any, y: int) -> None:
    x0 = _STORY_MARGIN_X
    draw.line(
        [(x0, y), (x0 + _STORY_CONTENT_W, y)],
        fill=_STORY_COLOR_LINE,
        width=1,
    )


def _story_draw_param_block(
    draw: Any,
    *,
    label: str,
    value: str,
    x: int,
    y: int,
    label_font: Any,
    body_font: Any,
    max_chars: int = 20,
) -> None:
    draw.text((x, y), label, fill=_STORY_COLOR_LAVENDER, font=label_font)
    line1, line2 = _story_wrap_words(value, max_chars)
    draw.text((x, y + 38), line1, fill=_STORY_COLOR_WHITE, font=body_font)
    if line2:
        draw.text((x, y + 76), line2, fill=_STORY_COLOR_WHITE, font=body_font)


def _story_draw_channel_panel(
    draw: Any,
    *,
    panel_y: int,
    panel_h: int,
    domain: str,
    channel_id: str,
    superpower: str,
    trigger: str,
    header_font: Any,
    superpower_font: Any,
    trigger_font: Any,
) -> None:
    pad_x = _STORY_MARGIN_X + _STORY_PANEL_PAD_X
    inner_w = _STORY_PANEL_INNER_WIDTH
    header = f"{domain.upper()} // КАНАЛ {channel_id}"
    draw.text((pad_x, panel_y + 28), header, fill=_STORY_COLOR_WHITE_RGB, font=header_font)
    _draw_story_multiline_text(
        draw,
        superpower,
        (pad_x, panel_y + 88),
        superpower_font,
        max_width=inner_w,
        fill=_STORY_COLOR_WHITE_RGB,
        line_spacing=_STORY_LINE_SPACING,
        max_lines=3,
    )
    divider_y = panel_y + panel_h - 72
    draw.line(
        [(pad_x, divider_y), (pad_x + inner_w, divider_y)],
        fill=_STORY_COLOR_WHITE_RGB,
        width=1,
    )
    trigger_text = f"ТРИГГЕР: {trigger.rstrip('.')}".upper()
    _draw_story_multiline_text(
        draw,
        trigger_text,
        (pad_x, divider_y + 16),
        trigger_font,
        max_width=720,
        fill=_STORY_COLOR_WHITE_RGB,
        line_spacing=_STORY_LINE_SPACING,
        max_lines=3,
    )


def _story_channel_trigger(channel_code: str) -> str:
    from services.hd_channel_archetypes import normalize_channel_code

    code = normalize_channel_code(channel_code)
    block = load_static_block("channels", code) if code else {}
    trigger = str(
        block.get("shadow") or block.get("gift") or block.get("theme") or "точка роста в решениях"
    ).strip().rstrip(".")
    return trigger


def _build_story_active_channels_info(math_data: dict[str, object]) -> list[dict[str, str]]:
    channels_raw = math_data.get("active_channels") or []
    channels = [str(ch).strip() for ch in channels_raw if str(ch).strip()]
    domains = ("Деньги", "Отношения", "Энергия")
    items: list[dict[str, str]] = []
    for idx, domain in enumerate(domains):
        if idx >= len(channels):
            break
        channel = channels[idx]
        from services.hd_channel_archetypes import normalize_channel_code

        code = normalize_channel_code(channel) or f"0{idx + 1}"
        raw_text = format_channel_superpower_for_user(channel)
        raw_trigger = _story_channel_trigger(channel)
        text, trigger = _story_humanize_channel_copy(raw_text, raw_trigger)
        items.append(
            {
                "domain": domain,
                "channel_num": code,
                "text": text,
                "trigger": trigger,
            }
        )
    return items


def _story_channel_card_line(channel_code: str) -> str:
    """Короткая строка для Stories card 2: суперсила + триггер (0 LLM, до 150 символов)."""
    from services.hd_channel_archetypes import normalize_channel_code

    code = normalize_channel_code(channel_code)
    block = load_static_block("channels", code) if code else {}
    superpower = format_channel_superpower_for_user(code or channel_code)
    trigger = str(
        block.get("shadow") or block.get("gift") or block.get("theme") or "точка роста в решениях"
    ).strip()
    trigger = trigger.rstrip(".")
    superpower, trigger = _story_humanize_channel_copy(superpower, trigger)
    if len(trigger) > 56:
        trigger = trigger[:56].rsplit(" ", 1)[0] + "…"
    line = f"{superpower}. Триггер: {trigger}"
    if len(line) > 150:
        line = line[:147].rsplit(" ", 1)[0] + "…"
    return line


def _build_story_card2_sections(math_data: dict[str, object]) -> list[tuple[str, str]]:
    """Три блока Stories card 2 из active_channels (без выдержек «Боли» из PDF)."""
    channels_raw = math_data.get("active_channels") or []
    channels = [str(ch).strip() for ch in channels_raw if str(ch).strip()]
    labels = (
        "💼 Деньги",
        "❤️ Отношения",
        "⚡ Энергия",
    )
    sections: list[tuple[str, str]] = []
    for idx, domain_label in enumerate(labels):
        if idx >= len(channels):
            break
        body = _story_channel_card_line(channels[idx])
        if body:
            sections.append((domain_label, body))
    return sections


def generate_instagram_stories(
    uid: int,
    report: dict[str, Any],
    *,
    math_data: dict[str, object] | None = None,
    hd_type: str = "",
    birth_data: str = "",
) -> list[str]:
    """
    Instagram Stories premium minimal: бодиграф + параметры; card 2 — суперсилы каналов (0 LLM).

    Returns:
        ``tmp/story_{uid}_1.jpg``, ``tmp/story_{uid}_2.jpg``.
    """
    _ = report  # AI-текст разбора в Stories не используется — только карта и static blocks.
    if Image is None or ImageDraw is None or ImageFilter is None:
        raise RuntimeError("Установите пакет Pillow для Instagram Stories.")

    ensure_story_fonts_available()

    if math_data is None:
        math_data = build_hd_math_data(hd_type or "не указан", birth_data or "")

    os.makedirs(str(_HD_BODYGRAPH_OUTPUT_DIR), exist_ok=True)
    bodygraph_path = _HD_BODYGRAPH_OUTPUT_DIR / f"ready_hd_{uid}.png"
    paths: list[str] = []
    meta = hd_profile_metadata(math_data)
    profile_label = profile_archetype_label(str(meta.get("profile") or "")) or "—"
    hd_data = {
        "type": str(meta.get("hd_type") or hd_type or "Human Design"),
        "profile": profile_label,
        "authority": str(meta.get("authority") or "—"),
        "strategy": str(meta.get("strategy") or "—"),
    }
    birth_line = strip_hd_markdown_for_plain(
        str(meta.get("birth_data") or birth_data or "").strip()
    )
    birth_meta = birth_line[:64] if birth_line else "—"
    active_channels_info = _build_story_active_channels_info(math_data)
    background = _create_story_minimal_background()

    font_title = _load_story_font(56, bold=True)
    font_meta = _load_story_font(24, bold=False)
    font_param_label = _load_story_font(24, bold=False)
    font_param_value = _load_story_font(32, bold=True)
    font_domain_header = _load_story_font(38, bold=True)
    font_superpower = _load_story_font(32, bold=False)
    font_trigger = _load_story_font(32, bold=True)
    font_footer = _load_story_font(18, bold=False)
    footer_label = _story_footer_label()
    footer_fill = _story_rgba_fill_on_dark(255, 255, 255, 60)
    mx = _STORY_MARGIN_X
    param_left_x = mx + 40
    param_right_x = _STORY_PARAM_RIGHT_COL_X

    # --- Карточка 1: фон + бодиграф + стекло (RGBA), затем весь текст на RGB ---
    card1 = background.copy()
    body_img, body_glow = _extract_bodygraph_layers(bodygraph_path)
    if body_img is not None and body_glow is not None:
        body_img.thumbnail((680, 680), Image.Resampling.LANCZOS)
        body_glow.thumbnail((680, 680), Image.Resampling.LANCZOS)
        x_pos = (_STORY_CANVAS_SIZE[0] - body_img.size[0]) // 2
        y_pos = 380
        card1.paste(body_glow, (x_pos, y_pos), body_glow)
        card1.paste(body_img, (x_pos, y_pos), body_img)
    card1 = _draw_story_glass_panel(card1, (mx, 1120, mx + _STORY_CONTENT_W, 1680), radius=30)
    card1 = card1.convert("RGB")
    draw1 = ImageDraw.Draw(card1)
    draw1.text((mx, 110), "HUMAN DESIGN", fill=_STORY_COLOR_WHITE_RGB, font=font_title)
    draw1.text(
        (mx, 190),
        f"PREMIUM ID: {str(uid)[:8].upper()}  //  {birth_meta.upper()}",
        fill=(160, 150, 180),
        font=font_meta,
    )
    draw1.line([(mx, 245), (mx + _STORY_CONTENT_W, 245)], fill=(255, 255, 255), width=1)
    for label, val, x, y in (
        ("ТИП ЛИЧНОСТИ", hd_data["type"], param_left_x, 1160),
        ("ПРОФИЛЬ", hd_data["profile"], param_right_x, 1160),
        ("ВНУТРЕННИЙ АВТОРИТЕТ", hd_data["authority"], param_left_x, 1420),
        ("СТРАТЕГИЯ ЖИЗНИ", hd_data["strategy"], param_right_x, 1420),
    ):
        draw1.text((x, y), label, fill=_STORY_COLOR_LABEL_RGB, font=font_param_label)
        col_width = 400 if x == param_left_x else mx + _STORY_CONTENT_W - x - _STORY_PANEL_PAD_X
        _draw_story_multiline_text(
            draw1,
            str(val).upper(),
            (x, y + 42),
            font_param_value,
            max_width=col_width,
            fill=_STORY_COLOR_WHITE_RGB,
            line_spacing=_STORY_LINE_SPACING,
            max_lines=2,
        )
    draw1.text(
        (540, 1835),
        footer_label,
        fill=footer_fill,
        font=font_footer,
        anchor="mm",
    )
    out1 = _HD_BODYGRAPH_OUTPUT_DIR / f"story_{uid}_1.jpg"
    _save_story_jpg(card1, out1)
    paths.append(f"tmp/story_{uid}_1.jpg")

    # --- Карточка 2: стеклянные плашки на RGBA, весь текст после convert("RGB") ---
    card2 = background.copy()
    panel_h = 320
    panel_y = 280
    panel_x2 = mx + _STORY_CONTENT_W
    channel_panels: list[tuple[int, dict[str, str]]] = []
    for item in active_channels_info[:3]:
        card2 = _draw_story_glass_panel(
            card2,
            (mx, panel_y, panel_x2, panel_y + panel_h),
            radius=24,
        )
        channel_panels.append((panel_y, item))
        panel_y += 335
    card2 = card2.convert("RGB")
    draw2 = ImageDraw.Draw(card2)
    draw2.text((mx, 110), "АКТИВНЫЕ КОДЫ", fill=_STORY_COLOR_WHITE_RGB, font=font_title)
    draw2.text(
        (mx, 190),
        f"АРХЕТИПЫ СУПЕРСИЛ ДЛЯ ТИПА: {hd_data['type'].upper()}",
        fill=(160, 150, 180),
        font=font_meta,
    )
    draw2.line([(mx, 245), (mx + _STORY_CONTENT_W, 245)], fill=(255, 255, 255), width=1)
    for py, item in channel_panels:
        _story_draw_channel_panel(
            draw2,
            panel_y=py,
            panel_h=panel_h,
            domain=item["domain"],
            channel_id=item["channel_num"],
            superpower=item["text"],
            trigger=item["trigger"],
            header_font=font_domain_header,
            superpower_font=font_superpower,
            trigger_font=font_trigger,
        )
    draw2.text(
        (540, 1835),
        footer_label,
        fill=footer_fill,
        font=font_footer,
        anchor="mm",
    )
    out2 = _HD_BODYGRAPH_OUTPUT_DIR / f"story_{uid}_2.jpg"
    _save_story_jpg(card2, out2)
    paths.append(f"tmp/story_{uid}_2.jpg")
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
    if _openrouter_configured():
        try:
            return await _generate_compat_via_openrouter(system_prompt, user_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenRouter compatibility failed, trying Gemini: %s", exc)
    elif not _gemini_configured():
        raise RuntimeError("hd_compat_unavailable: задайте OPENROUTER_API_KEY или GEMINI_API_KEY")
    if _gemini_configured():
        try:
            return await _generate_compat_via_gemini(system_prompt, user_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Gemini compatibility fallback failed")
            raise RuntimeError("hd_compat_unavailable") from exc
    raise RuntimeError("hd_compat_unavailable")


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
