"""Контролируемый сборщик мусора для высоконагруженного aiogram-бота.

Production-инвариант, зафиксированный в ``.cursorrules`` §1.4: CPython
auto-GC на проде ВЫКЛЮЧЕН (``gc.disable()``), стартовые объекты заморожены
(``gc.freeze()``). Сборка идёт ИСКЛЮЧИТЕЛЬНО под надзором фоновой
корутины ``controlled_gc_loop``.

Архитектура цикла:

* три фазы ``gc.collect(0) → gc.collect(1) → gc.collect(2)``;
* каждая фаза уходит в default-executor через ``loop.run_in_executor``
  — event loop не блокируется ни на одну фазу;
* между фазами ``await asyncio.sleep(0)`` отдаёт loop готовым callback'ам;
* длительность фазы > ``SLOW_GC_THRESHOLD_SEC`` → ``WARNING``, иначе DEBUG;
* падение в одной фазе НЕ останавливает остальные и НЕ ломает цикл.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time
from typing import Final

from services import metrics

logger = logging.getLogger(__name__)


DEFAULT_GC_INTERVAL_SEC: Final[int] = 600
SLOW_GC_THRESHOLD_SEC: Final[float] = 0.5


def setup_optimized_gc() -> None:
    """Отключить auto-GC и заморозить стартовые объекты в permanent gen.

    Идемпотентна: повторный вызов при уже выключенном GC — no-op,
    второго ``gc.freeze()`` не делает (это критично, чтобы дважды не
    зафризить кэши, инициализированные между вызовами).
    """

    if not gc.isenabled():
        logger.debug("gc: already disabled, skipping setup")
        return

    gc.freeze()
    gc.disable()
    logger.info(
        "gc: optimized mode enabled (auto-collect=OFF, frozen=%s)",
        gc.get_freeze_count(),
    )


async def _collect_generation_async(gen: int) -> tuple[int, float]:
    """Запустить ``gc.collect(gen)`` в default-executor'е.

    Возвращает ``(collected_objects, elapsed_seconds)``. Сам ``gc.collect``
    блокирующий — поэтому он уходит в thread pool, а основной loop
    остаётся свободным для обработки Telegram-апдейтов.
    """

    loop = asyncio.get_running_loop()
    start = time.perf_counter()
    collected: int = await loop.run_in_executor(None, gc.collect, gen)
    elapsed = time.perf_counter() - start
    return collected, elapsed


async def run_gc_cycle() -> dict[str, float]:
    """Полный tick GC: gen0 → sleep(0) → gen1 → sleep(0) → gen2 → sleep(0).

    Падение одной фазы (например, ``__del__``-исключение в cyclic-объекте)
    логируется как ``ERROR`` с stacktrace, но **не** прерывает следующие
    фазы — это критично для устойчивости фонового воркера.
    """

    stats: dict[str, float] = {}
    for gen in (0, 1, 2):
        try:
            collected, elapsed = await _collect_generation_async(gen)
        except asyncio.CancelledError:
            raise
        except Exception:
            metrics.incr("gc.phase.failed", {"gen": str(gen)})
            logger.error(
                "gc: collect(generation=%s) failed", gen, exc_info=True
            )
            continue

        stats[f"gen{gen}_objects"] = float(collected)
        stats[f"gen{gen}_seconds"] = elapsed

        metrics.observe(
            "gc.phase.duration_ms",
            elapsed * 1000.0,
            {"gen": str(gen)},
        )
        if elapsed > SLOW_GC_THRESHOLD_SEC:
            metrics.incr("gc.phase.slow", {"gen": str(gen)})

        log_fn = (
            logger.warning if elapsed > SLOW_GC_THRESHOLD_SEC else logger.debug
        )
        log_fn(
            "gc: gen=%s collected=%s elapsed=%.4fs",
            gen,
            collected,
            elapsed,
        )

        await asyncio.sleep(0)

    metrics.incr("gc.cycle.completed")
    return stats


async def controlled_gc_loop(
    interval_sec: int = DEFAULT_GC_INTERVAL_SEC,
) -> None:
    """Фоновая корутина: каждые ``interval_sec`` секунд → ``run_gc_cycle()``.

    Корректно завершается по ``CancelledError``. Любая иная ошибка цикла
    логируется как ``ERROR`` с ``exc_info=True``, цикл продолжается со
    следующего тика — мы не хотим потерять GC из-за разового глитча.
    """

    logger.info(
        "gc loop started interval=%ss (controlled mode)", int(interval_sec)
    )
    while True:
        try:
            await asyncio.sleep(interval_sec)
            await run_gc_cycle()
        except asyncio.CancelledError:
            logger.info("gc loop cancelled")
            raise
        except Exception:
            logger.error("gc loop: tick failed", exc_info=True)


__all__ = (
    "DEFAULT_GC_INTERVAL_SEC",
    "SLOW_GC_THRESHOLD_SEC",
    "setup_optimized_gc",
    "run_gc_cycle",
    "controlled_gc_loop",
)
