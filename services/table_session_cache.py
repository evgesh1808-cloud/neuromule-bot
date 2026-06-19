"""In-memory кэш данных таблицы для бесплатного переключения графиков."""

from __future__ import annotations

import time
from dataclasses import dataclass

from services.table_chart_types import ChartType

_SESSION_TTL_SEC = 6 * 60 * 60  # 6 ч


@dataclass
class TableSession:
    user_id: int
    chat_id: int
    rows: list[list[str]]
    caption_html: str
    xlsx_bytes: bytes
    chart_message_id: int
    active_chart: ChartType
    context_text: str
    created_at: float
    report_id: int | None = None


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
