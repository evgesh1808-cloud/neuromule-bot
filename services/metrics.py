"""Тонкий observability-слой для NeuroMule 🐎⚡️.

Цель — иметь штатные счётчики критичных событий БЕЗ внешних зависимостей
(prometheus_client, statsd, OpenTelemetry). Когда понадобится экспорт в
Prometheus / Grafana, заменим backend, не трогая call-sites.

Контракт:

* :func:`incr(name, labels=None, value=1)` — увеличить счётчик. Labels
  превращаются в часть составного ключа (отсортированно для
  детерминизма).
* :func:`observe(name, value_ms, labels=None)` — записать наблюдение для
  гистограммы (агрегируем count + sum + min + max). Используем для
  длительностей в миллисекундах.
* :func:`snapshot()` — атомарный снимок всех метрик (dict, безопасно
  сериализуется в JSON для дашборда / health-endpoint'а).
* :func:`reset()` — обнуление; нужен только для тестов и админ-команд.

Потокобезопасность: counters/observations — обычные dict'ы, операции
``+=`` и присваивания на CPython атомарны при GIL. Для гистограмм
``observe`` использует один-строчный compound update — тоже атомарен.
Никакого ``asyncio.Lock`` не нужно: метрики — лучшие friends-of-the-GIL.

Дизайн-правила:

* Никаких ``__del__``, никаких background-тасков — модуль чисто
  in-process, snapshot читается синхронно.
* Имена метрик — snake_case с точкой как разделителем подсистем
  (``payment.success``, ``throttle.blocked``, ``gc.cycle.duration_ms``).
* Никаких generic ``except Exception`` — если call-site падает на
  ``incr``, это баг тестов, не прода.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TypedDict

logger = logging.getLogger(__name__)


# ── Внутренние стораджи (in-process, in-memory) ───────────────────────────


_COUNTERS: dict[str, int] = {}


class _Hist(TypedDict):
    count: int
    sum: float
    min: float
    max: float


_HISTOGRAMS: dict[str, _Hist] = {}


# ── Утилиты ──────────────────────────────────────────────────────────────


def _compose_key(name: str, labels: Mapping[str, str] | None) -> str:
    """Стабильный составной ключ ``name{k=v,k=v}`` (метки отсортированы)."""
    if not labels:
        return name
    parts = ",".join(f"{k}={labels[k]}" for k in sorted(labels))
    return f"{name}{{{parts}}}"


# ── Публичный API ────────────────────────────────────────────────────────


def incr(
    name: str,
    labels: Mapping[str, str] | None = None,
    value: int = 1,
) -> None:
    """Увеличивает счётчик на ``value`` (по умолчанию ``1``)."""
    key = _compose_key(name, labels)
    _COUNTERS[key] = _COUNTERS.get(key, 0) + int(value)


def observe(
    name: str,
    value: float,
    labels: Mapping[str, str] | None = None,
) -> None:
    """Записывает наблюдение в гистограмму ``name`` (count/sum/min/max).

    ``value`` — миллисекунды или иная численная метрика (size in bytes,
    queue depth и т.п.). Отрицательные значения допустимы (например,
    `delta_balance`); агрегаты корректно их учтут.
    """
    key = _compose_key(name, labels)
    hist = _HISTOGRAMS.get(key)
    if hist is None:
        _HISTOGRAMS[key] = {
            "count": 1,
            "sum": float(value),
            "min": float(value),
            "max": float(value),
        }
        return
    hist["count"] += 1
    hist["sum"] += float(value)
    if value < hist["min"]:
        hist["min"] = float(value)
    if value > hist["max"]:
        hist["max"] = float(value)


def snapshot() -> dict[str, dict]:
    """Атомарный снимок всех метрик.

    Возвращает ``{"counters": {...}, "histograms": {...}}`` — структуру,
    готовую к ``json.dumps`` (например, для админского health-endpoint'а
    или дашборда WebApp).
    """
    return {
        "counters": dict(_COUNTERS),
        "histograms": {k: dict(v) for k, v in _HISTOGRAMS.items()},
    }


def reset() -> None:
    """Полностью обнуляет стораджи (только для тестов и админ-команд)."""
    _COUNTERS.clear()
    _HISTOGRAMS.clear()


__all__ = ("incr", "observe", "snapshot", "reset")
