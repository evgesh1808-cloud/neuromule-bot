"""Лёгкий in-memory кэш для переключения графиков (без матриц и xlsx в RAM)."""

from __future__ import annotations

import time
from dataclasses import dataclass

from services.table_chart_types import ChartType

_SESSION_TTL_SEC = 6 * 60 * 60  # 6 ч


@dataclass
class TableSession:
    """Только метаданные сессии; строки таблицы — в ``table_reports`` (SQLite)."""

    user_id: int
    chat_id: int
    chart_message_id: int
    active_chart: ChartType
    report_id: int
    created_at: float


_sessions: dict[int, TableSession] = {}


def _purge_expired() -> None:
    now = time.time()
    stale = [uid for uid, s in _sessions.items() if now - s.created_at > _SESSION_TTL_SEC]
    for uid in stale:
        _sessions.pop(uid, None)


def store_table_session(session: TableSession) -> None:
    _purge_expired()
    _sessions[int(session.user_id)] = session


def get_table_session(user_id: int) -> TableSession | None:
    _purge_expired()
    return _sessions.get(int(user_id))


def update_active_chart(user_id: int, chart_type: ChartType) -> None:
    session = _sessions.get(int(user_id))
    if session is not None:
        session.active_chart = chart_type


def clear_table_session(user_id: int) -> None:
    _sessions.pop(int(user_id), None)
