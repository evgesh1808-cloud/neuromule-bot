"""Лёгкий async-context-manager для замера времени БД-запросов.

Используется в hot-paths ``services/repository.py`` для трёх типов
запросов:

* финансовые (``claim_payment_charge``, ``insert_payment_event``);
* реф-/аналитические (``referrals_count``, ``get_sales_stats``);
* диалоговые (``dialog_fetch_last``, ``dialog_prune_keep_last``).

Каждый замер пишет:

* ``db.query_ms{name="<query_name>"}`` — гистограмма длительности.

Дизайн-инварианты:

* НЕ оборачивать ВСЕ запросы — overhead на context-manager (~5 мкс)
  при ~миллионе мелких UPDATE'ов в день станет ощутим. Только
  явно перечисленные критичные ``async with``.
* НЕ глотать исключения — все ошибки пробрасываются как есть.
* Метрика пишется ровно один раз — в ``__aexit__``, независимо от
  исхода (success / exception).
"""
from __future__ import annotations

import time
from types import TracebackType
from typing import Final

from services import metrics


class TimedQuery:
    """``async with TimedQuery("query_name"):`` — пишет ``db.query_ms``.

    Имя метрики выбирается в момент создания context'а. На выходе
    через ``metrics.observe`` запоминается длительность в миллисекундах
    с метрикой ``db.query_ms{name=<query_name>}``.
    """

    __slots__ = ("_name", "_start")

    def __init__(self, name: str) -> None:
        self._name = name
        self._start = 0.0

    async def __aenter__(self) -> "TimedQuery":
        self._start = time.perf_counter()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        metrics.observe(
            "db.query_ms", elapsed_ms, {"name": self._name}
        )


# Лимит, после которого hot query считается «медленной» (для будущих
# алёртов в monitoring/alerts.yml).
SLOW_QUERY_THRESHOLD_MS: Final[float] = 100.0


__all__ = ("TimedQuery", "SLOW_QUERY_THRESHOLD_MS")
