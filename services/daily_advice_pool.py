"""Пул «Совета дня»: ночной refill (эфемериды + Gemini) + мгновенная сборка без LLM.

Ночной cron (~00:05 МСК): локальный pyswisseph → бриф погоды дня → Gemini Free Tier
на 5 HD-ключей (без жаргона). Request path: пересечение натала с небом дня →
строка из ``daily_advice_pool`` → ``Template.safe_substitute`` (~мс, 0 API).

Эмбарго на OpenRouter в cron: только ``_configure_genai`` / ``_GEMINI_MODEL_CHAIN``.
Если пул пуст — встроенные шаблоны с ротацией по дню недели МСК.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from string import Template
from typing import Any

from services.hd_logic import (
    _GEMINI_MODEL_CHAIN,
    _WEEKDAY_RU,
    _configure_genai,
    _extract_gemini_text,
    _parse_json_object,
)
from services.repository import (
    get_daily_advice_pool,
    list_daily_advice_pool_keys,
    upsert_daily_advice_pool,
)

logger = logging.getLogger(__name__)

_MSK = timezone(timedelta(hours=3))
_REFILL_HOUR_MSK = 0
_REFILL_MINUTE_MSK = 5
_GEMINI_POOL_TIMEOUT_SEC = 45.0
_BETWEEN_TYPES_SLEEP_SEC = 1.5
_MAX_RETRIES_PER_TYPE = 3

# Строго 5 ключей пула (никаких зодиаков).
HD_POOL_TYPES: tuple[tuple[str, str], ...] = (
    ("generator", "Генератор"),
    ("mg", "Манифестирующий Генератор"),
    ("manifestor", "Манифестор"),
    ("projector", "Проектор"),
    ("reflector", "Рефлектор"),
)
HD_POOL_KEYS: tuple[str, ...] = tuple(k for k, _ in HD_POOL_TYPES)

_REQUIRED_PLACEHOLDERS: tuple[str, ...] = (
    "$display_name",
    "$user_role",
)
_OPTIONAL_PLACEHOLDERS: tuple[str, ...] = ("$energy_wave",)

_SECTION_KEYS: tuple[str, ...] = ("barometer", "navigator", "step_plus", "energy_drain")

_DEFAULT_ROLE_LABELS = frozenset(
    {
        "",
        "по умолчанию",
        "default",
        "не указана",
        "не указано",
    }
)

# Премиум-fallback (0 API). Без дат рождения и HD-жаргона.
# Плейсхолдеры: $display_name, $user_role, $energy_wave (Template.safe_substitute).
_BUILTIN_SECTIONS: dict[str, dict[str, str]] = {
    "generator": {
        "barometer": (
            "Поле дня плотное и телесное: космос просит не теории, а живого отклика. "
            "То, что звенит внутри, сегодня важнее чужих расписаний."
        ),
        "navigator": (
            "$display_name — ты ГЕНЕРАТОР. В $user_role твоя сила не в том, чтобы тянуть всё подряд, "
            "а в том, чтобы отвечать только на зов, который даёт внутреннее «да». "
            "Сейчас в тебе звучит $energy_wave — пусть удовлетворение будет компасом, а не скорость."
        ),
        "step_plus": (
            "$display_name, три тихих вдоха — и одно дело, на которое тело отвечает теплом. "
            "Начни с него, остальное подождёт."
        ),
        "energy_drain": (
            "Не бери на себя чужое «надо» в $user_role, если внутри уже звучит мягкое «нет»."
        ),
    },
    "mg": {
        "barometer": (
            "Небо сегодня быстрое: импульсы приходят пачками. Красота дня — в коротких рывках "
            "с проверкой отклика, а не в бесконечном переключении."
        ),
        "navigator": (
            "$display_name — ты МАНИФЕСТИРУЮЩИЙ ГЕНЕРАТОР. В $user_role можно ускоряться, "
            "но только после вспышки настоящего интереса. "
            "Сегодня тебя несёт $energy_wave — скорость без направления крадёт магию."
        ),
        "step_plus": (
            "$display_name, выбери один микро-шаг на две минуты и доведи его до конца "
            "без второго экрана и без «ещё одного» таба."
        ),
        "energy_drain": (
            "Не распыляй себя в $user_role на десять стартов — сегодня побеждает завершённый импульс."
        ),
    },
    "manifestor": {
        "barometer": (
            "Воздух инициативы: день любит тех, кто ясно называет намерение. "
            "Тишина без сигнала сегодня дороже прямого слова."
        ),
        "navigator": (
            "$display_name — ты МАНИФЕСТОР. В $user_role твоя власть — обозначить курс и дать "
            "пространству откликнуться. Сейчас усиливается $energy_wave — "
            "тебе не нужно ждать разрешения на первый ход."
        ),
        "step_plus": (
            "$display_name, сформулируй одним предложением, что запускаешь сегодня, "
            "и озвучь это человеку, чьё присутствие реально важно."
        ),
        "energy_drain": (
            "Не держи удар в $user_role внутри себя — непроговорённая инициатива превращается в давление."
        ),
    },
    "projector": {
        "barometer": (
            "День тонкой настройки: меньше шума — больше точных узнаваний. "
            "Сегодня ценится ясность взгляда, а не объём усилий."
        ),
        "navigator": (
            "$display_name — ты ПРОЕКТОР. В $user_role твоя ценность раскрывается там, "
            "где тебя пригласили в суть. Сейчас в тебе $energy_wave — "
            "не распыляй фокус на сцены, где тебя не слышат."
        ),
        "step_plus": (
            "$display_name, четыре минуты без экрана — затем один точный совет "
            "только тому, кто действительно открыл дверь вопросом."
        ),
        "energy_drain": (
            "Не доказывай свою ценность в $user_role через перегруз и непрошеные вмешательства."
        ),
    },
    "reflector": {
        "barometer": (
            "День-зеркало: пространство вокруг пишет твоё самочувствие. "
            "Выбор среды сейчас важнее скорости решений."
        ),
        "navigator": (
            "$display_name — ты РЕФЛЕКТОР. В $user_role мудрость приходит циклами, не вспышками. "
            "Сегодня особенно слышна $energy_wave — позволь дню отзвучать, прежде чем закреплять выбор."
        ),
        "step_plus": (
            "$display_name, смени фон на пять минут: воздух, другая комната или тихая музыка — "
            "дай системе обновиться."
        ),
        "energy_drain": (
            "Не принимай жёстких решений в $user_role под чужим «прямо сейчас» — "
            "давление снаружи не равно твоему внутреннему сроку."
        ),
    },
}


def builtin_pool_row(
    hd_type_key: str,
    *,
    advice_date: str | None = None,
) -> dict[str, str]:
    """Builtin-секции на дату: разные варианты по дню недели МСК."""
    key = hd_type_key if hd_type_key in _BUILTIN_SECTIONS else "generator"
    day = (advice_date or advice_date_iso_msk()).strip()
    base = dict(_BUILTIN_SECTIONS[key])
    spice = _WEEKDAY_SPICE.get(key, _WEEKDAY_SPICE["generator"])
    try:
        wd = date.fromisoformat(day).weekday()  # 0=пн … 6=вс
    except ValueError:
        wd = 0
    # Индекс строго по дню недели МСК → соседние дни всегда разные.
    idx = wd % 7
    bar_extra, step_extra, drain_extra = spice[idx]
    # Подмешиваем дневной акцент, чтобы текст не совпадал с вчерашним.
    base["barometer"] = f"{base['barometer']} {bar_extra}".strip()
    base["step_plus"] = f"{base['step_plus']} {step_extra}".strip()
    base["energy_drain"] = f"{base['energy_drain']} {drain_extra}".strip()
    base["model_id"] = f"builtin:wd{idx}"
    return base


# Короткий «вкус дня» (7 шт.) — крутится по дате, чтобы не было копии вчерашнего текста.
_WEEKDAY_SPICE: dict[str, tuple[tuple[str, str, str], ...]] = {
    "generator": (
        ("Сегодня особенно важно тело как компас.", "Сделай это до полудня.", "Не спорь с усталостью."),
        ("Ритм дня медленный и честный.", "Закрой один хвост.", "Не геройствуй."),
        ("Поле любит завершённые круги.", "Отметь «готово» вслух.", "Не открывай новое раньше времени."),
        ("Мягкий магнетизм: пусть приходит само.", "Спроси тело шёпотом.", "Не дави логикой."),
        ("Фильтр шума включён.", "Пауза 10 секунд перед «да».", "Не спасай чужой хаос."),
        ("Легкость — верный вектор.", "Смени комнату на 3 минуты.", "Не носи маску героя."),
        ("Один верный шаг сильнее десяти суетливых.", "Отложи ложное срочное.", "Не путай календарь с зовом."),
    ),
    "mg": (
        ("Импульсы пачками — выбирай.", "Две минуты — один финиш.", "Не коллекционируй старты."),
        ("Можно зажечь и отпустить.", "Таймер 12 минут.", "Брось мёртвое без стыда."),
        ("Планы должны дышать.", "Окно «для импульса» 15 минут.", "Не убивай отклик графиком."),
        ("Отделяй блеск от настоящего «да».", "Спроси: «ещё вкусно?»", "Закрой лишние вкладки."),
        ("Малая победа = большой заряд.", "Закрой мини-цикл.", "Не прыгай дальше раньше времени."),
        ("Игривый эфир — для эксперимента.", "Один странный шаг из любопытства.", "Не объясняй всем разворот."),
        ("Работай волнами.", "90 секунд пустоты после рывка.", "Не игнорируй спад."),
    ),
    "manifestor": (
        ("Ясный сигнал сильнее шёпота.", "Озвучь запуск.", "Не копи инициативу."),
        ("Информируй — и иди.", "Короткий статус «я делаю X».", "Не исчезай без слова."),
        ("Малый запуск под твоим контролем.", "Первый шаг за 5 минут.", "Не жди комитета."),
        ("Границы звучат чисто.", "Одно спокойное «нет».", "Не оправдывайся."),
        ("Тихий лидер тоже лидер.", "Намерение утром — действие до обеда.", "Не собирай реакции всех."),
        ("Верни авторство дня.", "Вычеркни чужое дело.", "Не тащи чужой сценарий."),
        ("Один ударный ход.", "Поставь флаг запуска.", "Не тони в черновиках."),
    ),
    "projector": (
        ("Точность важнее объёма.", "Совет — только по запросу.", "Не доказывай ценность."),
        ("Говори мало и в точку.", "Один уточняющий вопрос.", "Без непрошеной экспертизы."),
        ("Не каждая встреча твоя.", "Сократи опустошающий созвон.", "Не сиди «на всякий случай»."),
        ("Один фокус — одна система.", "Одно наблюдение — и стоп.", "Не растаскивай внимание."),
        ("Пусть найдут тебя.", "Один видимый сигнал о себе.", "Не охоться за вниманием."),
        ("Право не успевать «как все».", "Дневник: где меня увидели?", "Не сравнивай темп."),
        ("Одна верная реплика.", "Ответь на самый живой запрос.", "Не раздавай себя заранее."),
    ),
    "reflector": (
        ("Среда пишет самочувствие.", "Смени фон на 5 минут.", "Чужой срок — не твой."),
        ("Отделяй своё от атмосферы.", "Выйди из комнаты на 3 минуты.", "Не носи чужие эмоции."),
        ("Цикл важнее вспышки.", "Пересмотри завтра одно «да».", "Не подписывай под давлением."),
        ("Правильные люди = кислород.", "10 минут с тем, где легко.", "Уйди из токсичного поля."),
        ("Наблюдай без самокритики.", "Запиши наблюдение без вывода.", "Не бей себя отражением."),
        ("Гибкость — сила.", "Смени свет или плейлист.", "Не требуй чужого постоянства."),
        ("Малый круг питательнее сцены.", "Скажи «нет» рассеивающему событию.", "Не разливайся на всех."),
    ),
}


async def seed_builtin_pool_for_missing(advice_date: str | None = None) -> int:
    """Дописывает builtin-секции для отсутствующих ключей (без LLM)."""
    day = (advice_date or advice_date_iso_msk()).strip()
    have = set(await list_daily_advice_pool_keys(day))
    written = 0
    for key in HD_POOL_KEYS:
        if key in have:
            continue
        row = builtin_pool_row(key, advice_date=day)
        await upsert_daily_advice_pool(
            advice_date=day,
            hd_type_key=key,
            barometer=row["barometer"],
            navigator=row["navigator"],
            step_plus=row["step_plus"],
            energy_drain=row["energy_drain"],
            raw_json=None,
            model_id=row.get("model_id") or "builtin",
        )
        written += 1
    if written:
        logger.warning(
            "daily advice pool seeded %s builtin rows for %s",
            written,
            day,
        )
    return written


def advice_date_iso_msk(*, now: datetime | None = None) -> str:
    """Дата пула / лимита в календарном дне МСК."""
    moment = now or datetime.now(_MSK)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_MSK)
    else:
        moment = moment.astimezone(_MSK)
    return moment.date().isoformat()


def yesterday_advice_date_iso_msk(*, now: datetime | None = None) -> str:
    moment = now or datetime.now(_MSK)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_MSK)
    else:
        moment = moment.astimezone(_MSK)
    return (moment.date() - timedelta(days=1)).isoformat()


def resolve_hd_pool_key(hd_type_label: str) -> str:
    """Русский/сырой тип из БД → ключ пула. Неизвестный / НЕ ОПРЕДЕЛЕН → generator."""
    raw = (hd_type_label or "").strip().lower().replace("ё", "е")
    if not raw or "не определен" in raw or "не определён" in raw:
        return "generator"
    if "манифестир" in raw or raw in {"мг", "mg", "m.g.", "m/g"}:
        return "mg"
    if "манифестор" in raw or "manifestor" in raw:
        return "manifestor"
    if "проектор" in raw or "projector" in raw:
        return "projector"
    if "рефлектор" in raw or "reflector" in raw:
        return "reflector"
    if "генератор" in raw or "generator" in raw:
        return "generator"
    return "generator"


class _SafeFormatMap(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _normalize_role_for_copy(user_role: str) -> str:
    """Анкетный «по умолчанию» не должен звучать в премиум-тексте."""
    role = (user_role or "").strip()
    if role.lower() in _DEFAULT_ROLE_LABELS:
        return "своём ритме"
    return role


def _safe_format(template: str, **kwargs: str) -> str:
    """
    Безопасная подстановка: ``string.Template.safe_substitute``.

    Поддерживает и ``$display_name``, и legacy ``{display_name}`` из старых строк пула.
    Лишние ``{...}`` / ``$unknown`` не роняют процесс.
    """
    text = template or ""
    if not text:
        return ""
    # Legacy → Template syntax
    for key in kwargs:
        text = text.replace("{" + key + "}", "$" + key)
    try:
        return Template(text).safe_substitute(**{k: str(v) for k, v in kwargs.items()})
    except Exception:
        logger.debug("Template.safe_substitute failed, format_map fallback", exc_info=True)
        try:
            return text.format_map(_SafeFormatMap(**{k: str(v) for k, v in kwargs.items()}))
        except Exception:
            return text


def assemble_daily_advice_from_pool(
    pool_row: dict[str, str],
    *,
    display_name: str,
    birth_date: str = "",
    birth_time: str = "",
    birth_place: str = "",
    user_role: str = "",
    cta_text: str = "",
    energy_wave: str = "",
) -> str:
    """0 LLM: ``Template.safe_substitute`` + вычищение жаргона."""
    from services.hd_day_sky import strip_banned_jargon

    name = (display_name or "").strip() or "друг"
    role = _normalize_role_for_copy(user_role)
    wave = (energy_wave or "").strip() or "мягкая волна ясности"
    # birth_* — совместимость API; в премиум-копирайт не выводим (пустые подстановки).
    _ = (birth_date, birth_time, birth_place)
    ctx = {
        "display_name": name,
        "user_role": role,
        "energy_wave": wave,
        "birth_date": "",
        "birth_time": "",
        "birth_place": "",
    }
    barometer = _safe_format(pool_row.get("barometer", ""), **ctx)
    navigator = _safe_format(pool_row.get("navigator", ""), **ctx)
    if wave and wave not in navigator:
        navigator = f"{navigator} Сейчас в тебе звучит {wave}.".strip()
    step_plus = _safe_format(pool_row.get("step_plus", ""), **ctx)
    energy_drain = _safe_format(pool_row.get("energy_drain", ""), **ctx)
    cta = (cta_text or "").strip()
    parts = [
        "🌌 ЗВЕЗДНЫЙ БАРОМЕТР NEUROMULE 🐎⚡️",
        barometer,
        "",
        "🔮 ТВОЙ НАВИГАТОР",
        navigator,
        "",
        "🎯 ПРОСТОЙ ШАГ В ПЛЮС",
        f"• {step_plus}" if step_plus and not step_plus.lstrip().startswith("•") else step_plus,
        "",
        "⚠️ КУДА НЕ СЛИВАТЬ СИЛЫ",
        (
            f"• {energy_drain}"
            if energy_drain and not energy_drain.lstrip().startswith("•")
            else energy_drain
        ),
    ]
    body = "\n".join(parts).strip()
    if cta:
        body = f"{body}\n\n{cta}"
    return strip_banned_jargon(body)


def _build_pool_prompt(*, advice_date: str, hd_type_key: str, hd_type_ru: str) -> str:
    from services.hd_day_sky import day_sky_prompt_blurb

    try:
        d = date.fromisoformat(advice_date)
        weekday = _WEEKDAY_RU[d.weekday()]
    except ValueError:
        weekday = ""
    required = ", ".join(_REQUIRED_PLACEHOLDERS)
    optional = ", ".join(_OPTIONAL_PLACEHOLDERS)
    sky = day_sky_prompt_blurb(advice_date)
    return (
        "Ты — премиальный голос NeuroMule 🐎⚡️. Пиши просто, тепло и глубоко — "
        "как мудрый друг, а не как учебник Human Design.\n"
        f"Дата: {advice_date} ({weekday}). HD-тип шаблона: {hd_type_ru} ({hd_type_key}).\n\n"
        f"{sky}\n\n"
        "Верни JSON с четырьмя строками:\n"
        '  "barometer" — 1–2 предложения: погода дня для всех (чувства/состояния);\n'
        '  "navigator" — 2 предложения: совет этому типу простым языком;\n'
        '  "step_plus" — одно лёгкое действие на 2–5 минут;\n'
        '  "energy_drain" — одна ловушка ума.\n\n'
        f"Плейсхолдеры (dollar-syntax): в navigator обязательно {required}. "
        f"Желательно также {optional}. "
        "В step_plus/energy_drain — $display_name и/или $user_role.\n\n"
        "ЖЁСТКИЙ ЗАПРЕТ (ни слова): ворота, каналы, линии, транзитное Солнце, "
        "номера вроде 16-48 / 21 / 48, бодиграф, нейтрино, эклиптика, "
        "дата/время/город рождения, «роль по умолчанию», «анкета», «якорь».\n"
        "Описывай планетарные активации только как психологические состояния: "
        "«прилив лидерской энергии», «фокус на деталях», «творческий импульс», "
        "«волна мастерства», «потребность в паузе» и т.п.\n"
        "Без HTML/Markdown. Без слов «ИИ», «бот», «нейросеть». "
        "Только валидный JSON."
    )


def _normalize_sections(parsed: dict[str, Any]) -> dict[str, str]:
    from services.hd_day_sky import strip_banned_jargon

    out: dict[str, str] = {}
    for key in _SECTION_KEYS:
        val = parsed.get(key)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"missing section {key!r}")
        out[key] = strip_banned_jargon(val.strip())
        if not out[key]:
            raise ValueError(f"section {key!r} empty after jargon scrub")
    navigator = out["navigator"]
    if any(ph not in navigator for ph in _REQUIRED_PLACEHOLDERS):
        navigator = (
            f"{navigator}\n"
            "$display_name — держи свой ритм в $user_role без спешки. "
            "Сейчас в тебе звучит $energy_wave."
        )
    out["navigator"] = navigator
    return out


async def _gemini_json_for_type(
    *,
    prompt: str,
) -> tuple[dict[str, str], str]:
    """Один HD-тип: каскад Gemini. OpenRouter запрещён."""
    client = _configure_genai()
    errors: list[str] = []
    for model_name in _GEMINI_MODEL_CHAIN:
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={"response_mime_type": "application/json"},
                ),
                timeout=_GEMINI_POOL_TIMEOUT_SEC,
            )
            raw = _extract_gemini_text(response)
            if not raw:
                errors.append(f"{model_name}: empty")
                continue
            parsed = _parse_json_object(raw)
            sections = _normalize_sections(parsed)
            return sections, model_name
        except Exception as exc:  # noqa: BLE001
            logger.warning("pool Gemini %s failed: %s", model_name, exc)
            errors.append(f"{model_name}: {exc!r}")
            continue
    raise RuntimeError("gemini_pool_unavailable: " + "; ".join(errors))


async def refill_daily_advice_pool(
    advice_date: str | None = None,
    *,
    only_keys: list[str] | None = None,
) -> int:
    """
    Генерирует недостающие строки пула на дату (до 5 вызовов Gemini).

    Returns:
        Число успешно записанных типов.
    """
    day = (advice_date or advice_date_iso_msk()).strip()
    existing = set(await list_daily_advice_pool_keys(day))
    wanted = set(only_keys) if only_keys is not None else set(HD_POOL_KEYS)
    targets = [
        (key, label)
        for key, label in HD_POOL_TYPES
        if key in wanted and key not in existing
    ]
    if not targets:
        logger.info("daily advice pool already complete for %s", day)
        return 0

    written = 0
    for key, label in targets:
        prompt = _build_pool_prompt(advice_date=day, hd_type_key=key, hd_type_ru=label)
        last_err: Exception | None = None
        for attempt in range(1, _MAX_RETRIES_PER_TYPE + 1):
            try:
                sections, model_id = await _gemini_json_for_type(prompt=prompt)
                await upsert_daily_advice_pool(
                    advice_date=day,
                    hd_type_key=key,
                    barometer=sections["barometer"],
                    navigator=sections["navigator"],
                    step_plus=sections["step_plus"],
                    energy_drain=sections["energy_drain"],
                    raw_json=json.dumps(sections, ensure_ascii=False),
                    model_id=model_id,
                )
                written += 1
                logger.info(
                    "daily advice pool upsert date=%s key=%s model=%s",
                    day,
                    key,
                    model_id,
                )
                last_err = None
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning(
                    "pool refill attempt %s/%s failed date=%s key=%s: %s",
                    attempt,
                    _MAX_RETRIES_PER_TYPE,
                    day,
                    key,
                    exc,
                )
                await asyncio.sleep(2.0 * attempt)
        if last_err is not None:
            logger.error(
                "pool refill gave up date=%s key=%s: %s",
                day,
                key,
                last_err,
            )
        await asyncio.sleep(_BETWEEN_TYPES_SLEEP_SEC)
    return written


async def ensure_today_pool_filled() -> int:
    """Gemini-дозаполнение; если ключи всё ещё пусты — builtin seed."""
    day = advice_date_iso_msk()
    have = set(await list_daily_advice_pool_keys(day))
    missing = [k for k in HD_POOL_KEYS if k not in have]
    written = 0
    if missing:
        logger.info("daily advice pool missing keys for %s: %s", day, missing)
        try:
            written = await refill_daily_advice_pool(day, only_keys=missing)
        except Exception:
            logger.exception("ensure_today_pool_filled Gemini refill failed")
    seeded = await seed_builtin_pool_for_missing(day)
    return written + seeded


async def fetch_pool_with_stale_fallback(
    hd_type_key_or_label: str,
    *,
    now: datetime | None = None,
) -> dict[str, str] | None:
    """Сегодня → вчера. Без LLM."""
    raw = (hd_type_key_or_label or "").strip().lower()
    key = raw if raw in HD_POOL_KEYS else resolve_hd_pool_key(hd_type_key_or_label)
    today = advice_date_iso_msk(now=now)
    row = await get_daily_advice_pool(today, key)
    if row:
        return row
    yesterday = yesterday_advice_date_iso_msk(now=now)
    row = await get_daily_advice_pool(yesterday, key)
    if row:
        logger.warning(
            "daily advice pool stale hit key=%s date=%s",
            key,
            yesterday,
        )
    return row


async def resolve_pool_row_for_request(hd_type_key_or_label: str) -> dict[str, str]:
    """
    Request path: только кэш НА СЕГОДНЯ (МСК) → иначе builtin на сегодня.

    Вчерашний stale НЕ отдаём: из‑за него текст повторялся день за днём.
    """
    raw = (hd_type_key_or_label or "").strip().lower()
    key = raw if raw in HD_POOL_KEYS else resolve_hd_pool_key(hd_type_key_or_label)
    day = advice_date_iso_msk()

    try:
        row = await get_daily_advice_pool(day, key)
        if row:
            mid = (row.get("model_id") or "").strip()
            # Старые сиды без дневной ротации — перезаписываем на вариант дня.
            if mid in {"", "builtin"}:
                builtin = builtin_pool_row(key, advice_date=day)
                try:
                    await upsert_daily_advice_pool(
                        advice_date=day,
                        hd_type_key=key,
                        barometer=builtin["barometer"],
                        navigator=builtin["navigator"],
                        step_plus=builtin["step_plus"],
                        energy_drain=builtin["energy_drain"],
                        model_id=builtin.get("model_id") or "builtin",
                    )
                except Exception:
                    logger.exception("builtin pool refresh failed key=%s", key)
                return builtin
            return row
    except Exception:
        logger.exception("get_daily_advice_pool failed key=%s day=%s", key, day)

    builtin = builtin_pool_row(key, advice_date=day)
    try:
        await upsert_daily_advice_pool(
            advice_date=day,
            hd_type_key=key,
            barometer=builtin["barometer"],
            navigator=builtin["navigator"],
            step_plus=builtin["step_plus"],
            energy_drain=builtin["energy_drain"],
            model_id=builtin.get("model_id") or "builtin",
        )
    except Exception:
        logger.exception("builtin pool upsert failed key=%s", key)
    return builtin


def _seconds_until_next_msk_clock(
    hour: int,
    minute: int = 0,
    *,
    now: datetime | None = None,
) -> float:
    moment = now or datetime.now(_MSK)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_MSK)
    else:
        moment = moment.astimezone(_MSK)
    target = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if moment >= target:
        target = target + timedelta(days=1)
    return max(5.0, (target - moment).total_seconds())


async def daily_advice_pool_refill_loop() -> None:
    """Фон: при старте дозаполнить сегодня; далее каждый день в 00:05 МСК."""
    # Стартовый проход — не блокируем polling дольше, чем нужно: ошибки глотаем.
    try:
        await ensure_today_pool_filled()
    except Exception:
        logger.exception("daily_advice_pool startup refill failed")

    while True:
        delay = _seconds_until_next_msk_clock(_REFILL_HOUR_MSK, _REFILL_MINUTE_MSK)
        logger.info(
            "daily_advice_pool_refill_loop sleep %.0fs until next %02d:%02d MSK",
            delay,
            _REFILL_HOUR_MSK,
            _REFILL_MINUTE_MSK,
        )
        await asyncio.sleep(delay)
        try:
            # Полный refill на новую дату (existing пуст → все 5).
            await refill_daily_advice_pool(advice_date_iso_msk())
            await seed_builtin_pool_for_missing(advice_date_iso_msk())
        except Exception:
            logger.exception("daily_advice_pool_refill_loop tick failed")
            try:
                await seed_builtin_pool_for_missing(advice_date_iso_msk())
            except Exception:
                logger.exception("builtin seed after refill tick failed")
        # Защита от двойного тика в ту же минуту.
        await asyncio.sleep(60.0)
