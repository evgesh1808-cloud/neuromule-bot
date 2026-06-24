"""Под-режимы аналитики роли table_generator (Pydantic + Literal)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TableSubroleId = Literal[
    "standard_report",
    "wb_ozon_finance",
    "traffic_marketing",
    "mass_seo_generation",
]

DEFAULT_TABLE_SUBROLE: TableSubroleId = "standard_report"

VALID_TABLE_SUBROLES: frozenset[str] = frozenset(
    {
        "standard_report",
        "wb_ozon_finance",
        "traffic_marketing",
        "mass_seo_generation",
    }
)


class TableSubroleSelection(BaseModel):
    """Сохранённый в FSM выбор под-режима таблиц."""

    subrole_id: TableSubroleId = Field(default=DEFAULT_TABLE_SUBROLE)


def normalize_table_subrole(raw: str | None) -> TableSubroleId:
    sid = (raw or "").strip().lower()
    if sid in VALID_TABLE_SUBROLES:
        return sid  # type: ignore[return-value]
    return DEFAULT_TABLE_SUBROLE
