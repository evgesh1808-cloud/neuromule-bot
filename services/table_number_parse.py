"""Безопасное приведение ячеек таблицы к числам (Telegram, Excel, WB)."""

from __future__ import annotations

import re

_CURRENCY_TOKENS = (
    "руб.",
    "руб",
    "rub.",
    "rub",
    "₽",
    "$",
    "€",
    "%",
)
_NON_NUMERIC_RE = re.compile(r"[^\d.\-]")


def safe_float(val: object) -> float:
    """
    Приводит ячейку к ``float``; при ошибке возвращает ``0.0``.

    Поддерживает ``int``/``float``, пробелы-разделители тысяч, ``руб.``, запятую.
    """
    if val is None:
        return 0.0
    if isinstance(val, bool):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)

    text = str(val).replace("\u00a0", " ").strip()
    if not text:
        return 0.0

    lowered = text.lower()
    for token in _CURRENCY_TOKENS:
        lowered = lowered.replace(token, "")
    lowered = lowered.strip().replace(" ", "")

    if "," in lowered and "." in lowered:
        if lowered.rfind(",") > lowered.rfind("."):
            lowered = lowered.replace(".", "").replace(",", ".")
        else:
            lowered = lowered.replace(",", "")
    elif "," in lowered:
        left, _, right = lowered.partition(",")
        if right.isdigit() and len(right) <= 2:
            lowered = f"{left}.{right}"
        else:
            lowered = lowered.replace(",", "")

    cleaned = _NON_NUMERIC_RE.sub("", lowered)
    if not cleaned or cleaned in {".", "-", "-."}:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_table_number(val: object) -> float | None:
    """Как :func:`safe_float`, но ``None`` для пустых/нечисловых значений."""
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)

    raw = str(val).strip()
    if not raw:
        return None
    if not re.search(r"\d", raw):
        return None

    number = safe_float(val)
    return number


def format_rub_total(total: float) -> str:
    """Коммерческий формат: ``130,000.00 руб.``"""
    return f"{total:,.2f} руб."


def prepare_excel_value(val: object) -> int | float | str:
    """
    Значение для openpyxl: числа как ``int``/``float``, текст — как есть.

    Чтобы ``=SUM()`` считал ячейки, строки ``\"60 000 руб.\"`` превращаются в числа.
    """
    if val is None:
        return ""
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, (int, float)):
        return val

    text = str(val).replace("\u00a0", " ").strip()
    if not text:
        return ""

    lowered = text.lower()
    for token in _CURRENCY_TOKENS:
        lowered = lowered.replace(token, "")
    s = lowered.replace(" ", "").strip()
    if not s:
        return text

    try:
        if "." in s or "," in s:
            if "," in s and "." in s:
                if s.rfind(",") > s.rfind("."):
                    s = s.replace(".", "").replace(",", ".")
                else:
                    s = s.replace(",", "")
            elif s.count(",") == 1 and "." not in s:
                left, _, right = s.partition(",")
                if right.isdigit() and len(right) <= 2:
                    s = f"{left}.{right}"
                else:
                    s = s.replace(",", "")
            else:
                s = s.replace(",", "")
            number = float(s)
        else:
            number = float(int(s))

        if abs(number - round(number)) < 1e-9:
            return int(round(number))
        return number
    except ValueError:
        return text


def coerce_excel_numeric(val: object) -> int | float | str:
    """Алиас для обратной совместимости."""
    return prepare_excel_value(val)
