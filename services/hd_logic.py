"""HD Premium: Gemini report generation, SQLite helpers, and PDF export."""
from __future__ import annotations

import asyncio
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
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
except ImportError:  # pragma: no cover - surfaced at runtime in the handler.
    colors = None
    A4 = None
    simpleSplit = None
    pdfmetrics = None
    TTFont = None
    canvas = None

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

# Канал B (Gemini): флагманские Pro-модели для премиум-разбора и совместимости.
_GEMINI_MODEL_CHAIN: tuple[str, ...] = (
    "gemini-2.5-pro",
    "gemini-2.0-pro-exp-02-15",
    "gemini-1.5-pro-latest",
)
_HD_WATERMARK = "🧬 Создано в @neuromule_bot"
_HD_NEON_HEX = "#8B5CF6"
_ENERGY_SCALE_KEYS = ("capacity", "immunity", "scale")
_COMPAT_REPORT_KEYS = ("attraction", "conflicts", "growth")
# Таймаут одной Gemini-модели в «Совете дня» (сек); дальше — следующая / OpenRouter.
_GEMINI_DAILY_TIMEOUT_SEC = 20.0
_OPENROUTER_DAILY_TIMEOUT_SEC = 45.0
_OPENROUTER_DAILY_MAX_TOKENS = 900
_GEMINI_PREMIUM_TIMEOUT_SEC = 90.0
_OPENROUTER_PREMIUM_TIMEOUT_SEC = 120.0
_HD_PREMIUM_MAX_OUTPUT_TOKENS = 8192
_PDF_FONT_NAME = "HDReportFont"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PREMIUM_REPORT_KEYS = ("fast_facts", "money", "love", "energy", "plan")
_FAST_FACTS_MAX_LEN = 300
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


def _configure_genai() -> "genai.Client":
    """Клиент Google Gen AI SDK (только канал B, без OpenRouter)."""
    if genai is None:
        raise RuntimeError("Установите пакет google-genai для HD-отчетов и совета дня.")
    api_key = (_app_settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()
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
    for model_name in _GEMINI_MODEL_CHAIN:
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
    for model_name in _GEMINI_MODEL_CHAIN:
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
        parts.append("⚡ Экспресс-анализ\n" + str(report["fast_facts"]))
    parts.extend(
        [
            "💎 Деньги\n" + str(report["money"]),
            "❤️ Отношения\n" + str(report["love"]),
            "⚡️ Энергия\n" + str(report["energy"]),
            "📅 План на 30 дней\n" + str(report["plan"]),
        ]
    )
    return "\n\n".join(parts)


def build_hd_math_data(hd_type: str, birth_data: str) -> dict[str, object]:
    """Собирает math_data для элитного промпта и API-ответа."""
    gates: dict[str, object] = {}
    defined_set: set[str] = set()
    try:
        gates_payload = get_calculated_gates(birth_data)
        raw_gates = gates_payload.get("gates")
        if isinstance(raw_gates, dict):
            gates = raw_gates
        defined_set, _ = _defined_centers_from_birth_data(birth_data)
    except Exception:
        logger.debug("build_hd_math_data: ephemeris/gates unavailable", exc_info=True)
    defined_centers = sorted(defined_set)
    open_centers = [name for name in _ALL_HD_CENTER_NAMES if name not in defined_set]
    return {
        "hd_type": hd_type,
        "birth_data": birth_data,
        "defined_centers": defined_centers,
        "open_centers": open_centers,
        "gates": gates,
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
    }


def premium_report_to_json(report: dict[str, Any]) -> str:
    if all(k in report for k in _PREMIUM_REPORT_KEYS) and "energy_scales" in report:
        payload: dict[str, Any] = {
            **{k: str(report[k]).strip() for k in _PREMIUM_REPORT_KEYS},
            "energy_scales": _normalize_energy_scales(report.get("energy_scales")),
        }
    else:
        payload = _normalize_premium_report(report)
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
                    "⚡ Экспресс-анализ доступен в интерактивном разборе."
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
) -> dict[str, str]:
    """Полный HD-разбор: элитный промпт → Gemini SDK → OpenRouter fallback."""
    math_data = build_hd_math_data(hd_type, birth_data)
    system_prompt, user_prompt = _build_elite_premium_hd_prompt(user_name, math_data)
    errors: list[str] = []

    if genai is not None:
        try:
            return await _generate_premium_via_gemini(system_prompt, user_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini premium report failed, trying OpenRouter: %s", exc)
            errors.append(f"gemini: {exc!r}")
    else:
        logger.error(
            "Пакет google-genai не установлен — полный разбор через OpenRouter. "
            "На VDS: pip install 'google-genai>=1.0'"
        )
        errors.append("google-genai_missing")

    try:
        report = await _generate_premium_via_openrouter(system_prompt, user_prompt)
        logger.info("HD premium report served via OpenRouter fallback")
        return report
    except Exception as exc:  # noqa: BLE001
        logger.exception("OpenRouter premium report fallback failed")
        errors.append(f"openrouter: {exc!r}")

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
    for model_name in _GEMINI_MODEL_CHAIN:
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=gen_cfg,
                ),
                timeout=_GEMINI_PREMIUM_TIMEOUT_SEC,
            )
            return _parse_premium_report_from_llm(_extract_gemini_text(response))
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


async def _generate_premium_via_openrouter(system_prompt: str, user_prompt: str) -> dict[str, Any]:
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

    if genai is not None:
        try:
            return await _generate_daily_via_gemini(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini daily advice failed, trying OpenRouter: %s", exc)
            errors.append(f"gemini: {exc!r}")
    else:
        logger.error(
            "Пакет google-genai не установлен — «Совет дня» через OpenRouter. "
            "На VDS: pip install 'google-genai>=1.0'"
        )
        errors.append("google-genai_missing")

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
    "вибраци",
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
    '"money": "### Где ты сливаешь\\nТы берёшь проекты из страха «останусь без денег».\\n\\n'
    '### Что делать\\n**Неделя 1:** веди список откликов тела перед каждым «да».", '
    '"love": "### Боль\\nТы читаешь ожидания партнёра и теряешь себя в роли «удобного».", '
    '"energy": "### Боль\\nЖмёшь газ, когда Сакрал уже пуст.", '
    '"plan": "### Дни 1–5\\nОтслеживай сигнал тела перед решениями."}'
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
        "вида '34-20', '19-49', 'Gate 57'.\n"
        "- energy_scales: три целых числа 1–100 — capacity (ёмкость ауры по моторам), "
        "immunity (стойкость к чужому мнению по открытым центрам), scale (индекс харизмы/влияния).\n"
        "- money, love, energy: Markdown-строки с ### подзаголовками и **жирным**; "
        "КАЖДЫЙ раздел начинается с честной психологической боли из-за Ложного Я этой механики. "
        "Объём каждого раздела — от 1200 до 3000 символов, стиль ICF-коучинг без эзотерики.\n"
        "- plan: Markdown-план на 30 дней (блоки 1–5 / 6–15 / 16–30) с действиями и метриками.\n"
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


def _draw_pdf_footer(pdf, font_name: str, page_width: float) -> None:
    pdf.setFont(font_name, 8)
    if colors is not None:
        pdf.setFillColor(colors.HexColor("#777777"))
    pdf.drawCentredString(page_width / 2, 24, _HD_WATERMARK)
    if colors is not None:
        pdf.setFillColor(colors.black)


def _draw_hd_legend_table(
    pdf,
    font_name: str,
    x: float,
    y: float,
    *,
    hd_type: str,
    profile: str,
    authority: str,
    strategy: str,
) -> float:
    """Контрастная таблица-легенда параметров карты на первой странице PDF."""
    if colors is None:
        return y
    rows = (
        ("Тип", hd_type or "—"),
        ("Профиль", profile or "—"),
        ("Авторитет", authority or "—"),
        ("Стратегия", strategy or "—"),
    )
    row_h = 18
    col_w = (220, 280)
    table_h = row_h * len(rows) + 8
    top = y
    pdf.setFillColor(colors.HexColor("#1A1A24"))
    pdf.roundRect(x, top - table_h, sum(col_w), table_h, 6, fill=1, stroke=0)
    pdf.setFillColor(colors.HexColor(_HD_NEON_HEX))
    pdf.setFont(font_name, 10)
    pdf.drawString(x + 8, top - 14, "Параметры карты")
    pdf.setFillColor(colors.white)
    pdf.setFont(font_name, 9)
    cy = top - 28
    for label, value in rows:
        pdf.drawString(x + 10, cy, label)
        pdf.drawString(x + col_w[0] + 6, cy, (value or "—")[:42])
        cy -= row_h
    return top - table_h - 12


def _draw_bodygraph(
    pdf,
    birth_data: str | None,
    font_name: str,
    x: float,
    y: float,
    *,
    user_id: int,
) -> float:
    defined, warning = _defined_centers_from_birth_data(birth_data)
    pdf.setFont(font_name, 13)
    pdf.drawString(x, y, "Бодиграф")
    y -= 16

    img_width = 220.0
    img_height = 220.0
    try:
        rel_path = generate_premium_bodygraph(sorted(defined), user_id)
        img_path = _PROJECT_ROOT / rel_path
        from reportlab.lib.utils import ImageReader

        pdf.drawImage(
            ImageReader(str(img_path)),
            x,
            y - img_height,
            width=img_width,
            height=img_height,
            preserveAspectRatio=True,
            mask="auto",
        )
        y -= img_height + 12
    except Exception as exc:
        logger.warning("premium bodygraph render failed uid=%s: %s", user_id, exc, exc_info=True)
        pdf.setFont(font_name, 9)
        pdf.drawString(x, y, "Схема бодиграфа временно недоступна.")
        y -= 16

    pdf.setFont(font_name, 9)
    summary = "Закрашенные центры: " + (", ".join(sorted(defined)) if defined else "не определены")
    pdf.drawString(x, y, summary[:90])
    if warning:
        pdf.drawString(x, y - 14, warning[:90])
        y -= 28
    else:
        y -= 14
    return y - 8


def _draw_wrapped_text(
    pdf,
    text: str,
    font_name: str,
    font_size: int,
    birth_data: str | None = None,
    *,
    user_id: int = 0,
    hd_type: str = "",
    profile: str = "",
    authority: str = "",
    strategy: str = "",
) -> None:
    if simpleSplit is None or A4 is None:
        raise RuntimeError("Установите пакет reportlab для PDF-отчетов.")
    width, height = A4
    left = 48
    right = 48
    top = height - 56
    bottom = 56
    line_height = font_size + 5
    y = top

    pdf.setFont(font_name, 16)
    pdf.drawString(left, y, "Ваш Дизайн Человека")
    y -= 28
    y = _draw_hd_legend_table(
        pdf,
        font_name,
        left,
        y,
        hd_type=hd_type,
        profile=profile,
        authority=authority,
        strategy=strategy,
    )
    y = _draw_bodygraph(pdf, birth_data, font_name, left, y, user_id=user_id)
    pdf.setFont(font_name, font_size)

    paragraphs = text.splitlines() or [text]
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            y -= line_height
            continue
        for line in simpleSplit(paragraph, font_name, font_size, width - left - right):
            if y <= bottom:
                _draw_pdf_footer(pdf, font_name, width)
                pdf.showPage()
                pdf.setFont(font_name, font_size)
                y = top
            pdf.drawString(left, y, line)
            y -= line_height
    _draw_pdf_footer(pdf, font_name, width)


def create_pdf(
    user_id: int,
    text: str,
    birth_data: str | None = None,
    *,
    hd_type: str = "",
    profile: str = "",
    authority: str = "",
    strategy: str = "",
) -> str:
    if canvas is None or A4 is None:
        raise RuntimeError("Установите пакет reportlab для PDF-отчетов.")
    path = Path(tempfile.gettempdir()) / f"report_{user_id}.pdf"
    font_name = _register_pdf_font()
    pdf = canvas.Canvas(str(path), pagesize=A4)
    _draw_wrapped_text(
        pdf,
        text,
        font_name,
        11,
        birth_data,
        user_id=user_id,
        hd_type=hd_type,
        profile=profile,
        authority=authority,
        strategy=strategy,
    )
    pdf.save()
    return str(path)


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
    bbox = draw.textbbox((0, 0), _HD_WATERMARK, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((width - tw) // 2, height - 80), _HD_WATERMARK, fill=(200, 200, 210, 220), font=font)


def generate_instagram_stories(uid: int, report: dict[str, Any]) -> list[str]:
    """
    Instagram Stories: glassmorphism-карточка с бодиграфом + текстовая карточка fast_facts.

    Returns:
        Список относительных путей ``tmp/story_{uid}_1.png``, ``tmp/story_{uid}_2.png``.
    """
    if Image is None or ImageDraw is None or ImageFilter is None:
        raise RuntimeError("Установите пакет Pillow для Instagram Stories.")

    os.makedirs(str(_HD_BODYGRAPH_OUTPUT_DIR), exist_ok=True)
    bodygraph_path = _HD_BODYGRAPH_OUTPUT_DIR / f"ready_hd_{uid}.png"
    paths: list[str] = []

    # --- Карточка 1: Glassmorphism + бодиграф ---
    card1 = Image.new("RGBA", _STORY_CANVAS_SIZE, (10, 10, 18, 255))
    if bodygraph_path.is_file():
        bg_src = Image.open(bodygraph_path).convert("RGBA")
        scale = 2.5
        bg_w = int(bg_src.width * scale)
        bg_h = int(bg_src.height * scale)
        bg_scaled = bg_src.resize((bg_w, bg_h), Image.Resampling.LANCZOS)
        bg_blurred = bg_scaled.filter(ImageFilter.GaussianBlur(radius=70))
        bx = (_STORY_CANVAS_SIZE[0] - bg_w) // 2
        by = (_STORY_CANVAS_SIZE[1] - bg_h) // 2
        card1.paste(bg_blurred, (bx, by), bg_blurred)
        sharp_w = min(_STORY_CANVAS_SIZE[0] - 120, bg_src.width)
        sharp_h = int(sharp_w * bg_src.height / max(bg_src.width, 1))
        sharp = bg_src.resize((sharp_w, sharp_h), Image.Resampling.LANCZOS)
        sx = (_STORY_CANVAS_SIZE[0] - sharp_w) // 2
        sy = (_STORY_CANVAS_SIZE[1] - sharp_h) // 2
        card1.paste(sharp, (sx, sy), sharp)

    overlay = Image.new("RGBA", _STORY_CANVAS_SIZE, (10, 10, 18, 80))
    card1 = Image.alpha_composite(card1, overlay)
    draw1 = ImageDraw.Draw(card1)
    title_font = _load_story_font(42)
    _draw_story_watermark(draw1, _STORY_CANVAS_SIZE[0], _STORY_CANVAS_SIZE[1], _load_story_font(22))
    draw1.text((60, 90), "Human Design Premium", fill=_HD_NEON_HEX, font=title_font)
    out1 = _HD_BODYGRAPH_OUTPUT_DIR / f"story_{uid}_1.png"
    card1.convert("RGB").save(out1, format="PNG")
    paths.append(f"tmp/story_{uid}_1.png")

    # --- Карточка 2: fast_facts на матовых плашках ---
    card2 = Image.new("RGBA", _STORY_CANVAS_SIZE, (8, 8, 14, 255))
    draw2 = ImageDraw.Draw(card2)
    header_font = _load_story_font(36)
    body_font = _load_story_font(26)
    fast_facts = str(report.get("fast_facts") or "").strip() or "⚡ Персональный экспресс-анализ"
    blocks = [b.strip() for b in re.split(r"(?=⚡|💼|🔋)", fast_facts) if b.strip()] or [fast_facts]
    y_pos = 120
    for block in blocks[:4]:
        lines = textwrap.wrap(block, width=38) or [block]
        box_h = 28 + len(lines) * 34
        draw2.rounded_rectangle((48, y_pos, 1032, y_pos + box_h), radius=24, fill=(0, 0, 0, 140))
        first = lines[0]
        accent = first.split(":", 1)[0] + ":" if ":" in first else "⚡ Insight:"
        body = first.split(":", 1)[1].strip() if ":" in first else first
        draw2.text((72, y_pos + 14), accent, fill=_HD_NEON_HEX, font=header_font)
        ty = y_pos + 52
        for line in ([body] + lines[1:]):
            draw2.text((72, ty), line, fill=(235, 235, 245, 255), font=body_font)
            ty += 34
        y_pos += box_h + 24
    _draw_story_watermark(draw2, _STORY_CANVAS_SIZE[0], _STORY_CANVAS_SIZE[1], _load_story_font(22))
    out2 = _HD_BODYGRAPH_OUTPUT_DIR / f"story_{uid}_2.png"
    card2.convert("RGB").save(out2, format="PNG")
    paths.append(f"tmp/story_{uid}_2.png")
    return paths


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
    for model_name in _GEMINI_MODEL_CHAIN:
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=gen_cfg,
                ),
                timeout=_GEMINI_PREMIUM_TIMEOUT_SEC,
            )
            return _parse_compat_report_from_llm(_extract_gemini_text(response))
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
    if genai is not None:
        try:
            return await _generate_compat_via_gemini(system_prompt, user_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini compatibility failed, OpenRouter fallback: %s", exc)
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
