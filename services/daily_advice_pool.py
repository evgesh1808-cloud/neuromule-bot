"""Пул «Совета дня»: ночной refill через Gemini SDK + мгновенная сборка без LLM.

Эмбарго: OpenRouter здесь ЗАПРЕЩЁН. Только ``_configure_genai`` / ``_GEMINI_MODEL_CHAIN``.
Request path пользователя не вызывает этот refill — только ``assemble_daily_advice_from_pool``.
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
    """Если на сегодня нет всех 5 ключей — дозаполняет через Gemini."""
    day = advice_date_iso_msk()
    have = set(await list_daily_advice_pool_keys(day))
    missing = [k for k in HD_POOL_KEYS if k not in have]
    if not missing:
        return 0
    logger.info("daily advice pool missing keys for %s: %s", day, missing)
    return await refill_daily_advice_pool(day, only_keys=missing)


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
        except Exception:
            logger.exception("daily_advice_pool_refill_loop tick failed")
        # Защита от двойного тика в ту же минуту.
        await asyncio.sleep(60.0)
