"""Тесты сохранения CFO-отчётов в PostgreSQL (services/db_reports.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.db_reports import save_user_report_to_db


@pytest.mark.asyncio
async def test_save_user_report_to_db_skips_error_metrics() -> None:
    ok = await save_user_report_to_db(1, {"error": "empty"})
    assert ok is False


@pytest.mark.asyncio
async def test_save_user_report_to_db_commits_row() -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=ctx)

    metrics = {
        "platform": "wildberries",
        "tax_type": "USN",
        "tax_rate": 6.0,
        "total_revenue": 100_000.0,
        "tax_total": 6_000.0,
        "net_profit": 20_000.0,
        "sku_data": {},
    }

    with patch("services.db_reports.get_reports_session_factory", return_value=factory):
        ok = await save_user_report_to_db(42, metrics)

    assert ok is True
    session.add.assert_called_once()
    session.commit.assert_awaited_once()
