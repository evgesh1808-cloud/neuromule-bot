"""Типы графиков Smart Chart для роли table_generator."""

from __future__ import annotations

from enum import StrEnum


class ChartType(StrEnum):
    AUTO = "auto"
    PIE = "pie"
    LINE = "line"
    BAR = "bar"
