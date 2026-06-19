"""Сериализатор ``metrics.snapshot()`` в Prometheus text-format 0.0.4.

Чистая функция от снэпшота — без HTTP, без I/O, без зависимостей.
Тестируется юнитами; используется из ``services.metrics_http`` для
роута ``GET /metrics``.

Преобразования:

* Имена метрик: ``.`` → ``_`` (Prometheus naming rules). Остальные
  валидные символы пропускаются как есть.
* Counters → ``# TYPE … counter`` + одна строка значений.
* Histograms (наш формат — count/sum/min/max) → ``# TYPE … summary``
  с тремя сэмплами на ключ:
  - ``<name>_count{labels} <count>``;
  - ``<name>_sum{labels} <sum>``;
  - ``<name>{labels,quantile="0"} <min>``;
  - ``<name>{labels,quantile="1"} <max>``.
  Полные buckets/quantiles мы не считаем (overhead на каждый
  ``observe`` слишком высок для bot-cases); min/max — компромисс,
  достаточный для алёрта «slow GC phase».
* Label values экранируются по правилам Prometheus
  (``\\``, ``\\n``, ``\\"``).
* Порядок строк детерминирован (sorted by key) — это важно для diff'а
  и для предсказуемых юнит-тестов.

Что НЕ делается (намеренно):

* Никаких timestamp'ов в строках — Prometheus сам ставит время скрейпа;
* Никаких ``# HELP`` строк — у нас нет per-metric описаний (для них
  понадобится отдельный реестр, имеет смысл только когда счётчиков
  станет ~50+);
* Никаких ``except Exception`` — если строка не парсится, это баг
  в call-site'е ``incr``/``observe``, не в exporter'е.
"""
from __future__ import annotations

import re
from typing import Any

# Конвертация имени NeuroMule (``a.b.c``) в Prometheus-имя (``a_b_c``).
_NAME_TRANSLATE = str.maketrans({".": "_"})

# Ключ из snapshot выглядит как ``name`` либо ``name{k=v,k=v}``.
# Группа 1 — имя, группа 2 — содержимое лейблов (без скобок).
_KEY_RE = re.compile(r"^([^{]+)(?:\{([^}]*)\})?$")


def _parse_key(key: str) -> tuple[str, list[tuple[str, str]]]:
    """Разбирает composite-ключ snapshot'а на ``(name, sorted_labels)``.

    Лейблы дополнительно сортируются (на случай если в snapshot пришёл
    ключ из ручного теста с произвольным порядком).
    """
    match = _KEY_RE.match(key)
    if not match:
        return key, []
    raw_name, raw_labels = match.group(1), match.group(2) or ""
    name = raw_name.translate(_NAME_TRANSLATE)
    labels: list[tuple[str, str]] = []
    if raw_labels:
        for pair in raw_labels.split(","):
            if "=" not in pair:
                continue
            k, _, v = pair.partition("=")
            labels.append((k.strip(), v.strip()))
        labels.sort()
    return name, labels


def _escape_label_value(value: str) -> str:
    """Экранирование по правилам Prometheus text-format."""
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _render_labels(
    labels: list[tuple[str, str]], extra: tuple[str, str] | None = None
) -> str:
    """Рендер ``{k="v",k="v"}`` или пустая строка."""
    items: list[tuple[str, str]] = list(labels)
    if extra is not None:
        items = items + [extra]
    if not items:
        return ""
    parts = ",".join(
        f'{k}="{_escape_label_value(v)}"' for k, v in items
    )
    return "{" + parts + "}"


def _format_number(value: float | int) -> str:
    """Сериализатор числа: целые → без точки, дробные → repr.

    Prometheus принимает оба формата, но целые без ``.0`` читаются лучше.
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def to_prometheus_text(snapshot: dict[str, dict[str, Any]]) -> str:
    """Сериализовать ``metrics.snapshot()`` в Prometheus exposition format.

    Args:
        snapshot: dict с ключами ``"counters"`` и ``"histograms"``.

    Returns:
        Готовый ``str`` со всеми метриками, разделённый ``\\n``. Заканчивается
        ``\\n`` (требование formata 0.0.4).
    """
    lines: list[str] = []
    counters: dict[str, int] = dict(snapshot.get("counters") or {})
    histograms: dict[str, dict] = dict(snapshot.get("histograms") or {})

    # ── Counters ─────────────────────────────────────────────────────────
    # Группируем по имени метрики, чтобы ``# TYPE`` напечатать ровно один раз.
    by_name_counters: dict[str, list[tuple[list[tuple[str, str]], int]]] = {}
    for raw_key, value in counters.items():
        name, labels = _parse_key(raw_key)
        by_name_counters.setdefault(name, []).append((labels, int(value)))

    for name in sorted(by_name_counters):
        lines.append(f"# TYPE {name} counter")
        for labels, value in sorted(by_name_counters[name]):
            lines.append(f"{name}{_render_labels(labels)} {_format_number(value)}")

    # ── Histograms (как Prometheus summary) ──────────────────────────────
    by_name_hists: dict[str, list[tuple[list[tuple[str, str]], dict]]] = {}
    for raw_key, hist in histograms.items():
        name, labels = _parse_key(raw_key)
        by_name_hists.setdefault(name, []).append((labels, hist))

    for name in sorted(by_name_hists):
        lines.append(f"# TYPE {name} summary")
        for labels, hist in sorted(by_name_hists[name], key=lambda x: x[0]):
            count = int(hist.get("count", 0))
            total = float(hist.get("sum", 0.0))
            mn = float(hist.get("min", 0.0))
            mx = float(hist.get("max", 0.0))
            lines.append(
                f"{name}_count{_render_labels(labels)} {_format_number(count)}"
            )
            lines.append(
                f"{name}_sum{_render_labels(labels)} {_format_number(total)}"
            )
            lines.append(
                f"{name}{_render_labels(labels, ('quantile', '0'))} "
                f"{_format_number(mn)}"
            )
            lines.append(
                f"{name}{_render_labels(labels, ('quantile', '1'))} "
                f"{_format_number(mx)}"
            )

    return "\n".join(lines) + ("\n" if lines else "")


__all__ = ("to_prometheus_text",)
