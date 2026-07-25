"""Страховка OpenRouter-ключа: дневные лимиты токенов / FREE-чатов / USD.

In-process storage (как ``services.metrics``): атомарные dict-assign под GIL.
Персист в SQLite можно добавить позже без смены call-site API.

Контракт:

* ``preflight_spend`` / ``check_*`` — без побочных эффектов (пустить / стоп).
* ``consume_free_daily_chat`` / ``record_token_usage`` — после успешного хода.
* ``reset()`` — только тесты.
* ``cap == 0`` → проверка пропускается.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal

from config import settings
from services.billing.types import TariffTier

SpendReason = Literal[
    "",
    "ok",
    "free_daily_chat_limit",
    "user_daily_tokens_cap",
    "global_usd_cap",
]


@dataclass(frozen=True, slots=True)
class SpendDecision:
    ok: bool
    reason: SpendReason = "ok"
    detail: str = ""


# date_iso -> user_id -> chats_used
_FREE_CHAT_HITS: dict[str, dict[int, int]] = {}
# date_iso -> user_id -> tokens_used
_USER_TOKENS: dict[str, dict[int, int]] = {}
# date_iso -> usd_spent
_GLOBAL_USD: dict[str, float] = {}


def reset() -> None:
    """
    Обнуление всех in-process счётчиков.

    В проде суточный срез идёт по UTC-ключу ``YYYY-MM-DD`` (см. ``utc_today_iso``):
    в полночь UTC данные автоматически «устаревают» без вызова ``reset``.
    Явный ``reset()`` — для тестов и ручного сброса.
    """
    _FREE_CHAT_HITS.clear()
    _USER_TOKENS.clear()
    _GLOBAL_USD.clear()


def utc_today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def estimate_usage_usd(
    prompt_tokens: int,
    completion_tokens: int,
    *,
    usd_per_1k_prompt: float | None = None,
    usd_per_1k_completion: float | None = None,
) -> float:
    """Грубая оценка $ без invoice OpenRouter (для soft global cap)."""
    p_rate = (
        float(settings.openrouter_usd_per_1k_prompt)
        if usd_per_1k_prompt is None
        else float(usd_per_1k_prompt)
    )
    c_rate = (
        float(settings.openrouter_usd_per_1k_completion)
        if usd_per_1k_completion is None
        else float(usd_per_1k_completion)
    )
    p = max(0, int(prompt_tokens)) / 1000.0 * max(0.0, p_rate)
    c = max(0, int(completion_tokens)) / 1000.0 * max(0.0, c_rate)
    return round(p + c, 8)


def daily_token_cap_for_tariff(tariff: TariffTier | str | None) -> int:
    """Маппинг тарифа → дневной token cap из ``config.settings``."""
    if isinstance(tariff, TariffTier):
        tier = tariff
    else:
        tier = TariffTier.from_db(None if tariff is None else str(tariff))
    mapping = {
        TariffTier.FREE: int(settings.user_daily_tokens_cap_free),
        TariffTier.MINI: int(settings.user_daily_tokens_cap_mini),
        TariffTier.SMART: int(settings.user_daily_tokens_cap_smart),
        TariffTier.ULTRA: int(settings.user_daily_tokens_cap_ultra),
    }
    return mapping.get(tier, int(settings.user_daily_tokens_cap_mini))


def _as_tier(tariff: TariffTier | str | None) -> TariffTier:
    if isinstance(tariff, TariffTier):
        return tariff
    return TariffTier.from_db(None if tariff is None else str(tariff))


def check_free_daily_chat(
    user_id: int,
    *,
    tariff: TariffTier | str | None,
    limit: int | None = None,
    enforce: bool | None = None,
    day: str | None = None,
) -> SpendDecision:
    """FREE: лимит числа чат-запросов в сутки (UTC)."""
    lim = int(settings.free_daily_chat_limit if limit is None else limit)
    enf = bool(settings.free_daily_chat_enforce if enforce is None else enforce)
    if not enf or lim <= 0:
        return SpendDecision(ok=True, reason="ok")
    if _as_tier(tariff) is not TariffTier.FREE:
        return SpendDecision(ok=True, reason="ok")

    day_key = day or utc_today_iso()
    used = int(_FREE_CHAT_HITS.get(day_key, {}).get(int(user_id), 0))
    if used >= lim:
        return SpendDecision(
            ok=False,
            reason="free_daily_chat_limit",
            detail=f"used={used} limit={lim}",
        )
    return SpendDecision(ok=True, reason="ok", detail=f"used={used} limit={lim}")


def consume_free_daily_chat(user_id: int, *, day: str | None = None) -> int:
    """Инкремент FREE-чат счётчика. Возвращает новое значение."""
    day_key = day or utc_today_iso()
    bucket = _FREE_CHAT_HITS.setdefault(day_key, {})
    uid = int(user_id)
    bucket[uid] = int(bucket.get(uid, 0)) + 1
    return bucket[uid]


def check_user_daily_tokens(
    user_id: int,
    *,
    projected_tokens: int,
    cap: int,
    day: str | None = None,
) -> SpendDecision:
    """Per-user дневной token cap. ``cap <= 0`` → проверка пропускается."""
    if int(cap) <= 0:
        return SpendDecision(ok=True, reason="ok")
    day_key = day or utc_today_iso()
    used = int(_USER_TOKENS.get(day_key, {}).get(int(user_id), 0))
    projected = max(0, int(projected_tokens))
    if used + projected > int(cap):
        return SpendDecision(
            ok=False,
            reason="user_daily_tokens_cap",
            detail=f"used={used} projected={projected} cap={cap}",
        )
    return SpendDecision(ok=True, reason="ok", detail=f"used={used} cap={cap}")


def check_global_usd_cap(
    *,
    projected_usd: float,
    cap_usd: float | None = None,
    day: str | None = None,
) -> SpendDecision:
    """Глобальный USD soft-cap. ``cap_usd <= 0`` → проверка пропускается."""
    cap = float(settings.openrouter_daily_usd_cap if cap_usd is None else cap_usd)
    if cap <= 0:
        return SpendDecision(ok=True, reason="ok")
    day_key = day or utc_today_iso()
    used = float(_GLOBAL_USD.get(day_key, 0.0))
    projected = max(0.0, float(projected_usd))
    if used + projected > cap:
        return SpendDecision(
            ok=False,
            reason="global_usd_cap",
            detail=f"used={used:.4f} projected={projected:.4f} cap={cap}",
        )
    return SpendDecision(ok=True, reason="ok", detail=f"used={used:.4f} cap={cap}")


def record_token_usage(
    user_id: int,
    tokens_used: int,
    cost_usd: float,
    *,
    day: str | None = None,
) -> None:
    """Фиксация расхода после ответа модели (user tokens + global $)."""
    day_key = day or utc_today_iso()
    total = max(0, int(tokens_used))
    usd = max(0.0, float(cost_usd))
    user_bucket = _USER_TOKENS.setdefault(day_key, {})
    uid = int(user_id)
    user_bucket[uid] = int(user_bucket.get(uid, 0)) + total
    _GLOBAL_USD[day_key] = float(_GLOBAL_USD.get(day_key, 0.0)) + usd


def preflight_spend(
    user_id: int,
    tariff: TariffTier | str | None,
    projected_tokens: int,
    *,
    projected_usd: float | None = None,
    day: str | None = None,
) -> SpendDecision:
    """
    Единая проверка до вызова OpenRouter.

    Читает caps из ``settings``. ``cap == 0`` → соответствующая проверка skip.
    Если ``projected_usd`` не задан — оценка по prompt-rate × projected_tokens.
    """
    tokens = max(0, int(projected_tokens))
    if projected_usd is None:
        usd = estimate_usage_usd(tokens, 0)
    else:
        usd = max(0.0, float(projected_usd))

    token_cap = daily_token_cap_for_tariff(tariff)

    for decision in (
        check_free_daily_chat(user_id, tariff=tariff, day=day),
        check_user_daily_tokens(
            user_id,
            projected_tokens=tokens,
            cap=token_cap,
            day=day,
        ),
        check_global_usd_cap(projected_usd=usd, day=day),
    ):
        if not decision.ok:
            return decision
    return SpendDecision(ok=True, reason="ok")


def snapshot(*, day: str | None = None) -> dict[str, object]:
    """Диагностика / админка (immutable copy)."""
    day_key = day or utc_today_iso()
    return {
        "day": day_key,
        "free_chats": dict(_FREE_CHAT_HITS.get(day_key, {})),
        "user_tokens": dict(_USER_TOKENS.get(day_key, {})),
        "global_usd": float(_GLOBAL_USD.get(day_key, 0.0)),
        "today_utc": date.today().isoformat(),
    }
