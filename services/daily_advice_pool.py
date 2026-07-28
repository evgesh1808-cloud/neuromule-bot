"""Пул «Совета дня»: ночной refill через Gemini SDK + мгновенная сборка без LLM.

Эмбарго на OpenRouter в cron: только ``_configure_genai`` / ``_GEMINI_MODEL_CHAIN``.
Если пул пуст (Gemini недоступен) — встроенные шаблоны + опциональный
emergency Gemini refill одного ключа, чтобы юзер не видел «Высшие силы».
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
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
_REFILL_HOUR_MSK = 3
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
    "{display_name}",
    "{birth_date}",
    "{birth_time}",
    "{birth_place}",
    "{user_role}",
)

_SECTION_KEYS: tuple[str, ...] = ("barometer", "navigator", "step_plus", "energy_drain")

# Аварийные шаблоны (0 API): если Gemini не заполнил пул — юзер всё равно получает совет.
_BUILTIN_SECTIONS: dict[str, dict[str, str]] = {
    "generator": {
        "barometer": (
            "Сегодня энергия дня мягкая и практичная: лучше опираться на то, "
            "что реально откликается в теле, а не на чужие планы."
        ),
        "navigator": (
            "{display_name}, для ГЕНЕРАТОРА в роли {user_role} сегодня важно "
            "отвечать только на то, что даёт внутренний «да». "
            "Твой якорь — рождение {birth_date} {birth_time}, {birth_place}: "
            "держи ритм удовлетворения, а не гонки."
        ),
        "step_plus": (
            "{display_name}, 3 минуты: перечисли вслух 3 дела, на которые тело "
            "отвечает лёгким «да», и начни с одного."
        ),
        "energy_drain": (
            "Не соглашайся из роли {user_role} на «надо», если внутри тихое «нет»."
        ),
    },
    "mg": {
        "barometer": (
            "День быстрый и многозадачный: легко распылиться. Сила — в коротких "
            "импульсах с проверкой отклика."
        ),
        "navigator": (
            "{display_name}, МАНИФЕСТИРУЮЩИЙ ГЕНЕРАТОР в роли {user_role}: "
            "сегодня можно ускоряться, но только после короткого «да» внутри. "
            "Контекст рождения {birth_date} {birth_time}, {birth_place} — "
            "не путай скорость с правильным направлением."
        ),
        "step_plus": (
            "{display_name}, выбери одно мелкое действие на 2 минуты и сделай "
            "его до конца без переключений."
        ),
        "energy_drain": (
            "Не прыгай между десятью задачами роли {user_role} без паузы на отклик."
        ),
    },
    "manifestor": {
        "barometer": (
            "Воздух дня инициативный: кто ясно обозначает намерение — двигается легче."
        ),
        "navigator": (
            "{display_name}, МАНИФЕСТОР в роли {user_role}: сегодня сила в том, "
            "чтобы объявить курс и дать другим пространство. "
            "Рождение {birth_date} {birth_time}, {birth_place} напоминает: "
            "ты не обязан ждать разрешения на свой ход."
        ),
        "step_plus": (
            "{display_name}, напиши одним предложением, что запускаешь сегодня, "
            "и сообщи это нужному человеку."
        ),
        "energy_drain": (
            "Не тяни инициативу роли {user_role} в тишине — молчание сейчас дороже конфликта."
        ),
    },
    "projector": {
        "barometer": (
            "День внимательный и точечный: меньше шума — больше точности узнавания."
        ),
        "navigator": (
            "{display_name}, ПРОЕКТОР в роли {user_role}: сегодня береги фокус и "
            "жди приглашения в суть, а не в суету. "
            "Точка опоры — {birth_date} {birth_time}, {birth_place}: "
            "твоя ценность в ясности, не в объёме работы."
        ),
        "step_plus": (
            "{display_name}, 4 минуты тишины без экрана — затем один чёткий совет "
            "только тому, кто реально спросил."
        ),
        "energy_drain": (
            "Не доказывай ценность роли {user_role} через перегруз и непрошеные советы."
        ),
    },
    "reflector": {
        "barometer": (
            "День зеркальный: атмосфера вокруг сильно влияет на самочувствие — "
            "выбирай среду осознанно."
        ),
        "navigator": (
            "{display_name}, РЕФЛЕКТОР в роли {user_role}: сегодня важнее качество "
            "пространства, чем скорость решений. "
            "Рождение {birth_date} {birth_time}, {birth_place} — "
            "дай себе цикл, прежде чем закреплять выбор."
        ),
        "step_plus": (
            "{display_name}, смени фон на 5 минут: другая комната, воздух или тихая музыка."
        ),
        "energy_drain": (
            "Не принимай жёстких решений роли {user_role} под чужим давлением «прямо сейчас»."
        ),
    },
}


def builtin_pool_row(hd_type_key: str) -> dict[str, str]:
    """Статический шаблон секций для ключа (fallback без Gemini)."""
    key = hd_type_key if hd_type_key in _BUILTIN_SECTIONS else "generator"
    row = dict(_BUILTIN_SECTIONS[key])
    row["model_id"] = "builtin"
    return row


async def seed_builtin_pool_for_missing(advice_date: str | None = None) -> int:
    """Дописывает builtin-секции для отсутствующих ключей (без LLM)."""
    day = (advice_date or advice_date_iso_msk()).strip()
    have = set(await list_daily_advice_pool_keys(day))
    written = 0
    for key in HD_POOL_KEYS:
        if key in have:
            continue
        row = builtin_pool_row(key)
        await upsert_daily_advice_pool(
            advice_date=day,
            hd_type_key=key,
            barometer=row["barometer"],
            navigator=row["navigator"],
            step_plus=row["step_plus"],
            energy_drain=row["energy_drain"],
            raw_json=None,
            model_id="builtin",
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


def _safe_format(template: str, **kwargs: str) -> str:
    """``.format`` без падения на лишних/битых скобках от модели."""
    text = template or ""
    try:
        return text.format_map(_SafeFormatMap(**{k: str(v) for k, v in kwargs.items()}))
    except Exception:
        logger.debug("safe_format fallback", exc_info=True)
        return text


def assemble_daily_advice_from_pool(
    pool_row: dict[str, str],
    *,
    display_name: str,
    birth_date: str,
    birth_time: str,
    birth_place: str,
    user_role: str,
    cta_text: str,
) -> str:
    """0 LLM: локальная подстановка плейсхолдеров в секции пула."""
    name = (display_name or "").strip() or "друг"
    role = (user_role or "").strip() or "по умолчанию"
    ctx = {
        "display_name": name,
        "birth_date": (birth_date or "").strip() or "не указана",
        "birth_time": (birth_time or "").strip() or "не указано",
        "birth_place": (birth_place or "").strip() or "не указан",
        "user_role": role,
    }
    barometer = _safe_format(pool_row.get("barometer", ""), **ctx)
    navigator = _safe_format(pool_row.get("navigator", ""), **ctx)
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
        return f"{body}\n\n{cta}"
    return body


def _build_pool_prompt(*, advice_date: str, hd_type_key: str, hd_type_ru: str) -> str:
    try:
        d = date.fromisoformat(advice_date)
        weekday = _WEEKDAY_RU[d.weekday()]
    except ValueError:
        weekday = ""
    placeholders = ", ".join(_REQUIRED_PLACEHOLDERS)
    return (
        "Ты — харизматичный цифровой коуч NeuroMule 🐎⚡️, топ-эксперт по Дизайну Человека.\n"
        f"Дата совета: {advice_date} ({weekday}).\n"
        f"HD-тип для этого шаблона: {hd_type_ru} (ключ {hd_type_key}).\n\n"
        "Сгенерируй JSON-объект с ЧЕТЫРЬМЯ строковыми полями:\n"
        '  "barometer" — 1–2 предложения: общая планетарная погода дня для всех;\n'
        '  "navigator" — 2 предложения: совет именно этому HD-типу;\n'
        '  "step_plus" — одно бытовое действие на 2–5 минут;\n'
        '  "energy_drain" — одна ловушка ума / куда не сливать силы.\n\n'
        "ОБЯЗАТЕЛЬНО: в поле navigator должны встретиться ВСЕ плейсхолдеры "
        f"ровно в таком виде: {placeholders}.\n"
        "В step_plus и energy_drain используй хотя бы {display_name} и {user_role}.\n"
        "barometer — без персональных плейсхолдеров и без города рождения.\n\n"
        "Правила текста: без HTML и Markdown; акценты — эмодзи и КАПС; "
        "без слов «ИИ», «бот», «нейросеть»; тёплый бытовой тон.\n"
        "Верни ТОЛЬКО валидный JSON без markdown-оград."
    )


def _normalize_sections(parsed: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in _SECTION_KEYS:
        val = parsed.get(key)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"missing section {key!r}")
        out[key] = val.strip()
    navigator = out["navigator"]
    if any(ph not in navigator for ph in _REQUIRED_PLACEHOLDERS):
        navigator = (
            f"{navigator}\n"
            "(Контекст: {display_name}, роль {user_role}, "
            "рождение {birth_date} {birth_time}, {birth_place}.)"
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
    Request path (мгновенно): кэш сегодня/вчера → builtin seed в БД.

    Никогда не бросает: при любой ошибке БД возвращает builtin в памяти.
    """
    raw = (hd_type_key_or_label or "").strip().lower()
    key = raw if raw in HD_POOL_KEYS else resolve_hd_pool_key(hd_type_key_or_label)
    builtin = builtin_pool_row(key)

    try:
        row = await fetch_pool_with_stale_fallback(key)
        if row:
            return row
    except Exception:
        logger.exception("fetch_pool_with_stale_fallback failed key=%s", key)

    day = advice_date_iso_msk()
    try:
        await upsert_daily_advice_pool(
            advice_date=day,
            hd_type_key=key,
            barometer=builtin["barometer"],
            navigator=builtin["navigator"],
            step_plus=builtin["step_plus"],
            energy_drain=builtin["energy_drain"],
            model_id="builtin",
        )
    except Exception:
        logger.exception("builtin pool upsert failed key=%s", key)
    return builtin


def _seconds_until_next_msk_hour(hour: int, *, now: datetime | None = None) -> float:
    moment = now or datetime.now(_MSK)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_MSK)
    else:
        moment = moment.astimezone(_MSK)
    target = moment.replace(hour=hour, minute=0, second=0, microsecond=0)
    if moment >= target:
        target = target + timedelta(days=1)
    return max(5.0, (target - moment).total_seconds())


async def daily_advice_pool_refill_loop() -> None:
    """Фон: при старте дозаполнить сегодня; далее каждый день в 03:00 МСК."""
    # Стартовый проход — не блокируем polling дольше, чем нужно: ошибки глотаем.
    try:
        await ensure_today_pool_filled()
    except Exception:
        logger.exception("daily_advice_pool startup refill failed")

    while True:
        delay = _seconds_until_next_msk_hour(_REFILL_HOUR_MSK)
        logger.info(
            "daily_advice_pool_refill_loop sleep %.0fs until next %02d:00 MSK",
            delay,
            _REFILL_HOUR_MSK,
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
