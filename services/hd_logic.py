"""HD Premium: Gemini report generation, SQLite helpers, and PDF export."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import re
from collections.abc import AsyncIterator
from datetime import date, datetime
from pathlib import Path

import aiosqlite

from config import settings as _app_settings

try:
    import google.generativeai as genai
except ImportError:  # pragma: no cover - surfaced at runtime in the handler.
    genai = None

try:
    import swisseph as swe
except ImportError:  # pragma: no cover - surfaced at runtime in the handler.
    swe = None

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
PRICE_PDF = 70
PRICE_UPSCALE = 1
HD_REPORT_COST = PRICE_PDF
MATCH_REPORT_COST = 50

# Канал B (Gemini): приоритет 2.0, затем линейка 1.5 / алиасы (устраняет 404 у устаревших имён).
_GEMINI_MODEL_CHAIN: tuple[str, ...] = (
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-flash-latest",
)
_PDF_FONT_NAME = "HDReportFont"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PREMIUM_REPORT_KEYS = ("money", "love", "energy", "plan")
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

_DAILY_FORECAST_PROMPT = (
    "Ты — харизматичный цифровой коуч, официальный аватар системы NeuroMul и топ-эксперт "
    "по Дизайну Человека (Human Design). Твоя задача — сгенерировать короткий, "
    'вдохновляющий и строго персонализированный "Совет дня" от лица NeuroMul.\n\n'
    "ВХОДНЫЕ ДАННЫЕ ПОЛЬЗОВАТЕЛЯ ДЛЯ АНАЛИЗА:\n"
    "- Текущая дата генерации: {current_date}\n"
    "- День недели: {day_of_week}\n"
    "- Активный оффер дня: {current_cta_text}\n"
    "- Тип личности пользователя: {hd_type}\n"
    "- Роль/Сфера занятости в жизни: {user_role}\n"
    "- Точные данные рождения юзера: {birth_date}, {birth_time}, город {birth_place}\n"
    "- Статус подписки на канал @mulendeeva_ai: Активна (Проверено системой)\n\n"
    "ЖЕСТКИЕ ТЕХНИЧЕСКИЕ ПРАВИЛА ДЛЯ СТРИМИНГА ТЕКСТА (СТРОГО):\n"
    "1. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать любые HTML-теги (например, <b>, <i>, <a>). "
    "При пошаговой отправке чанков текста (через editMessageText) это намертво ломает "
    'парсинг Telegram и вызывает ошибку "Bad Request: can\'t parse entities".\n'
    "2. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать Markdown-символы (*, **, _, `) внутри слов "
    "или фраз. Они отображаются на экране как технический мусор во время потокового вывода.\n"
    "3. Выделяй ключевые мысли исключительно заглавными буквами (CAPS LOCK) и функциональными "
    "эмодзи в самом начале строк.\n"
    "4. Разделяй смысловые блоки строго одной пустой строкой (\\n\\n).\n"
    '5. ПОЛНОСТЬЮ ИСКЛЮЧИ аббревиатуру "ИИ" или словосочетание "Искусственный Интеллект" '
    "из текста. Робот выдает информацию от лица бренда NeuroMul.\n\n"
    "ИНСТРУКЦИЯ ПО КОНТЕНТУ И ТОНУ РЕЧИ:\n"
    "1. Запусти внутренний анализ NeuroMul для планетарной погоды на {current_date}. "
    "Свяжи текущие космические транзиты планет с индивидуальным Типом личности ({hd_type}), "
    "жизненной ролью ({user_role}) и натальной картой рождения "
    "({birth_date} {birth_time} {birth_place}).\n"
    '2. Пиши на живом, простом и теплом языке без занудного эзотерического сленга '
    '("вибрации", "нейтрино", "обуславливание") и без сухого бизнеса. Приводи понятные '
    "бытовые примеры (быт, дети, текущие задачи, общение с близкими, забота о себе).\n"
    "3. Общий объем основного текста — строго до 6-7 предложений. Будь краток, пиши емко и без воды.\n"
    '4. В самом конце сообщения, после блока "⚠️ КУДА НЕ СЛИВАТЬ СИЛЫ", добавь пустую '
    "строку (\\n\\n) и мягко выведи текст активного оффера дня: {current_cta_text}.\n\n"
    "СТРОГО СЛЕДУЙ СЛЕДУЮЩЕЙ СТРУКТУРУ ОТВЕТА (заголовки копируй один в один):\n\n"
    "🌌 ЗВЕЗДНЫЙ БАРОМЕТР NEUROMUL\n"
    "(Опиши текущую планетарную энергию на {current_date} через призму роли {user_role} "
    "и места рождения {birth_place}. Какое космическое давление сегодня на небе и в "
    "атмосфере вокруг людей? 1-2 коротких предложения)\n\n"
    "🔮 ТВОЙ НАВИГАТОР ({hd_type})\n"
    "(Дай персональный совет от NeuroMul, как типу {hd_type} в его роли {user_role}, "
    "рожденному в {birth_time}, правильно и бережно распределить силы именно сегодня. "
    "2 коротких предложения)\n\n"
    "🎯 ПРОСТОЙ ШАГ В ПЛЮС\n"
    "• (Одно конкретное, легкое практическое, физическое или бытовое действие на сегодня "
    "в рамках контекста роли {user_role}, чтобы быстро войти в ресурс)\n\n"
    "⚠️ КУДА НЕ СЛИВАТЬ СИЛЫ\n"
    "• (Предупреди, на какую мелкую суету, обиду, спешку или ошибку Ложного Я в роли "
    "{user_role} юзер может зря слить всю свою энергию сегодня. 1-2 предложения)"
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
    user_role = "предприниматель или эксперт"
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
    hd_type = _col("hd_type") or parsed["hd_type_inline"]
    if not hd_type:
        hd_type = "уточни мягко по натальной карте рождения"

    user_role = _col("advice_user_role") or parsed["user_role"]

    return {
        "hd_type": hd_type,
        "user_role": user_role,
        "birth_date": parsed["birth_date"],
        "birth_time": parsed["birth_time"],
        "birth_place": parsed["birth_place"],
    }


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
    "advice_birth_data",
    "advice_user_role",
}


def _configure_genai() -> None:
    """Настройка SDK Google Gemini (только канал B, без OpenRouter)."""
    if genai is None:
        raise RuntimeError("Установите пакет google-generativeai для HD-отчетов и совета дня.")
    api_key = (_app_settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()
    if not api_key or api_key.startswith(("your_", "ваш_")):
        raise RuntimeError("Задайте GEMINI_API_KEY в .env.")
    genai.configure(api_key=api_key)


async def gemini_generate_plain_text(prompt: str) -> str:
    """
    Один запрос текста к Gemini с перебором моделей (совместимость, отчёты без JSON-режима).
    Не использует OpenRouter.
    """
    _configure_genai()
    assert genai is not None
    errors: list[str] = []
    for model_name in _GEMINI_MODEL_CHAIN:
        try:
            model = genai.GenerativeModel(model_name)
            response = await model.generate_content_async(prompt)
            text = (getattr(response, "text", "") or "").strip()
            if text:
                return text
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


def _normalize_premium_report(parsed: dict[str, object]) -> dict[str, str]:
    report: dict[str, str] = {}
    for key in _PREMIUM_REPORT_KEYS:
        value = parsed.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Gemini JSON response is missing non-empty {key!r}")
        report[key] = value.strip()
    return report


def format_premium_report(report: dict[str, str]) -> str:
    return (
        "💎 Деньги\n"
        f"{report['money']}\n\n"
        "❤️ Отношения\n"
        f"{report['love']}\n\n"
        "⚡️ Энергия\n"
        f"{report['energy']}\n\n"
        "📅 План на 30 дней\n"
        f"{report['plan']}"
    )


def premium_report_to_json(report: dict[str, str]) -> str:
    return json.dumps(_normalize_premium_report(report), ensure_ascii=False)


def premium_report_from_json(raw: str | None) -> dict[str, str] | None:
    if not raw:
        return None
    try:
        parsed = _parse_json_object(raw)
        return _normalize_premium_report(parsed)
    except Exception:
        return None


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
    await get_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE users
            SET crystals = crystals + ?,
                balance = crystals + ?,
                balance_crystals = crystals + ?
            WHERE id = ?
            """,
            (delta, delta, delta, user_id),
        )
        await db.commit()


async def try_consume_crystals(user_id: int, amount: int) -> bool:
    if amount <= 0:
        return True
    await get_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE users
            SET crystals = crystals - ?,
                balance = crystals - ?,
                balance_crystals = crystals - ?
            WHERE id = ? AND crystals >= ?
            """,
            (amount, amount, amount, user_id, amount),
        )
        await db.commit()
        return cur.rowcount == 1


async def generate_premium_report(hd_type: str, birth_data: str) -> dict[str, str]:
    prompt = (
        "Ты — ведущий мировой эксперт по Дизайну Человека и стратегическому коучингу. "
        "Твоя задача — создать глубокий, премиальный аналитический разбор, который заменит "
        "пользователю многочасовую консультацию с профи.\n\n"
        f"Тип: {hd_type}\n"
        f"Данные рождения и контекст: {birth_data}\n\n"
        "Верни только валидный JSON без markdown. Схема:\n"
        '{ "money": "текст", "love": "текст", "energy": "текст", "plan": "текст" }\n\n'
        "ТРЕБОВАНИЯ К КОНТЕНТУ (УРОВЕНЬ СУПЕР-ЭКСПЕРТ):\n"
        "1. СТИЛЬ: Никакой «воды» и общих фраз. Пиши дерзко, глубоко, точно в цель, в стиле NeuroMule. "
        "Используй термины HD, но сразу объясняй их прикладное значение для жизни.\n"
        "2. СТРАТЕГИЯ И АВТОРИТЕТ: Это фундамент. Вплети их так, чтобы пользователь понял: это "
        "единственный верный для него способ проживать жизнь без сопротивления.\n"
        "3. АНАЛИЗ 9 ЦЕНТРОВ (Глубокое погружение):\n"
        "   - Для ОПРЕДЕЛЕННЫХ центров: Опиши их как «вечные двигатели» пользователя. "
        "Как именно на этой энергии делать деньги и строить влияние?\n"
        "   - Для ОТКРЫТЫХ центров: Опиши их как места «мудрости через уязвимость». "
        "Четко укажи на ложное «Я» — где пользователь пытается быть тем, кем не является, "
        "и сливает на этом ресурсы.\n"
        "4. MONEY: Проанализируй финансовый потенциал. Как этому Типу продавать, не выгорая? "
        "Где его «золотая жила» в карьере? (учитывай Эго и Сакрал).\n"
        "5. LOVE: Как взаимодействовать с аурой других людей. В чем главная ошибка пользователя "
        "в коммуникации согласно его Профилю? Как найти баланс между собой и партнером?\n"
        "6. ENERGY: Точный биохакинг. Где брать силы и как правильно отдыхать именно этому Типу "
        "(учитывай Корень и Селезенку).\n"
        "7. PLAN (30 ДНЕЙ): Это должна быть пошаговая инструкция трансформации. "
        "Не 'думай позитивно', а 'в день 1-5 делай ЭТО, отслеживай ТАКУЮ реакцию тела'.\n\n"
        "ВАЖНО: Каждый раздел должен давать ответ на вопрос 'И что мне теперь с этим делать?'. "
        "Разбор должен выглядеть как дорогая инвестиция в себя."
    )
    _configure_genai()
    assert genai is not None
    errors: list[str] = []
    for model_name in _GEMINI_MODEL_CHAIN:
        model = genai.GenerativeModel(model_name)
        try:
            response = await model.generate_content_async(
                prompt,
                generation_config={"response_mime_type": "application/json"},
            )
            parsed = _parse_json_object(getattr(response, "text", "") or "")
            return _normalize_premium_report(parsed)
        except Exception as exc_json:  # noqa: BLE001
            logger.warning(
                "Gemini %s: JSON-режим или разбор не удались, пробуем обычный ответ: %s",
                model_name,
                exc_json,
            )
            errors.append(f"{model_name}(json): {exc_json!r}")
            try:
                response = await model.generate_content_async(prompt)
                parsed = _parse_json_object(getattr(response, "text", "") or "")
                return _normalize_premium_report(parsed)
            except Exception as exc_plain:  # noqa: BLE001
                errors.append(f"{model_name}(plain): {exc_plain!r}")
                continue
    raise RuntimeError("gemini_unavailable: " + "; ".join(errors))


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
) -> AsyncIterator[str]:
    """Потоковый совет дня (канал B, только Gemini)."""
    prompt = build_daily_advice_prompt(user_profile, current_cta_text=current_cta_text)
    _configure_genai()
    assert genai is not None
    errors: list[str] = []
    for model_name in _GEMINI_MODEL_CHAIN:
        try:
            model = genai.GenerativeModel(model_name)
            stream = await model.generate_content_async(prompt, stream=True)
            async for chunk in stream:
                text = getattr(chunk, "text", "") or ""
                if text:
                    yield text
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini %s: поток совета дня недоступен: %s", model_name, exc)
            errors.append(f"{model_name}: {exc!r}")
            continue
    raise RuntimeError("gemini_unavailable: " + "; ".join(errors))


async def generate_daily_advice(
    user_profile: DailyAdviceUserProfile,
    *,
    current_cta_text: str,
) -> str:
    """Одиночный текст совета дня (без стрима)."""
    prompt = build_daily_advice_prompt(user_profile, current_cta_text=current_cta_text)
    return await gemini_generate_plain_text(prompt)


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


def _draw_pdf_footer(pdf, font_name: str, page_width: float) -> None:
    pdf.setFont(font_name, 8)
    if colors is not None:
        pdf.setFillColor(colors.HexColor("#777777"))
    pdf.drawCentredString(page_width / 2, 24, "Создано в @mulendeeva_ai")
    if colors is not None:
        pdf.setFillColor(colors.black)


def _draw_bodygraph(pdf, birth_data: str | None, font_name: str, x: float, y: float) -> float:
    if colors is None:
        return y
    defined, warning = _defined_centers_from_birth_data(birth_data)
    pdf.setFont(font_name, 13)
    pdf.drawString(x, y, "Бодиграф")
    y -= 16

    center_color = colors.HexColor("#8A5CFF")
    open_color = colors.white
    stroke_color = colors.HexColor("#444444")
    shapes = [
        ("Голова", x + 120, y - 8, 64, 28),
        ("Аджна", x + 120, y - 48, 64, 28),
        ("Горло", x + 115, y - 90, 74, 28),
        ("G-центр", x + 115, y - 132, 74, 32),
        ("Эго", x + 202, y - 130, 54, 28),
        ("Селезенка", x + 38, y - 170, 70, 30),
        ("Сакрал", x + 118, y - 178, 70, 34),
        ("Солнечное сплетение", x + 198, y - 170, 96, 30),
        ("Корень", x + 118, y - 230, 70, 32),
    ]
    pdf.setLineWidth(1.0)
    for name, sx, sy, width, height in shapes:
        pdf.setFillColor(center_color if name in defined else open_color)
        pdf.setStrokeColor(stroke_color)
        pdf.roundRect(sx, sy, width, height, 6, fill=1, stroke=1)
        pdf.setFillColor(colors.white if name in defined else colors.black)
        pdf.setFont(font_name, 7 if len(name) > 12 else 8)
        pdf.drawCentredString(sx + width / 2, sy + height / 2 - 3, name)

    pdf.setFont(font_name, 9)
    pdf.setFillColor(colors.black)
    summary = "Закрашенные центры: " + (", ".join(sorted(defined)) if defined else "не определены")
    pdf.drawString(x, y - 270, summary[:90])
    if warning:
        pdf.drawString(x, y - 284, warning[:90])
    return y - 304


def _draw_wrapped_text(pdf, text: str, font_name: str, font_size: int, birth_data: str | None = None) -> None:
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
    y -= 32
    y = _draw_bodygraph(pdf, birth_data, font_name, left, y)
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


def create_pdf(user_id: int, text: str, birth_data: str | None = None) -> str:
    if canvas is None or A4 is None:
        raise RuntimeError("Установите пакет reportlab для PDF-отчетов.")
    path = Path(tempfile.gettempdir()) / f"report_{user_id}.pdf"
    font_name = _register_pdf_font()
    pdf = canvas.Canvas(str(path), pagesize=A4)
    _draw_wrapped_text(pdf, text, font_name, 11, birth_data)
    pdf.save()
    return str(path)


def parse_hd_request(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    hd_type = "не указан"
    birth_lines: list[str] = []
    for line in text.splitlines():
        low = line.lower().strip()
        if low.startswith("тип:"):
            hd_type = line.split(":", 1)[1].strip() or hd_type
        else:
            birth_lines.append(line)
    birth_data = "\n".join(birth_lines).strip() or text
    return hd_type, birth_data
