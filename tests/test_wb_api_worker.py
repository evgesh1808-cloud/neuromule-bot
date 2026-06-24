"""Тесты workers/wb_api_worker.py (батч, очередь 09:00, уведомления)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from config import Settings
from services import repository as repo
from services.wb_api.types import WbBatchDigest
from workers import wb_api_worker

_MSK = timezone(timedelta(hours=3))


@pytest_asyncio.fixture
async def wb_db(tmp_path, monkeypatch):
    db_path = tmp_path / "wb_worker.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setattr(repo, "DB_PATH", str(db_path))
    await repo.init_db()
    yield db_path


@pytest.mark.asyncio
async def test_process_wb_user_persists_extended_report(wb_db, monkeypatch) -> None:
    user_id = 42
    await repo.ensure_user(user_id)
    await repo.upsert_wb_api_token(user_id, "token-test")

    mock_client = MagicMock()
    mock_client.fetch_product_rows = AsyncMock(
        return_value=[
            {
                "sku": "1",
                "name": "WRAPPER",
                "revenue": 50_000,
                "commission": 5_000,
                "logistics": 2_000,
                "ad_cost": 1_000,
                "stock_qty": 20,
                "sales_7d_qty": 35,
            }
        ]
    )
    monkeypatch.setattr(
        wb_api_worker,
        "generate_morning_insight",
        AsyncMock(return_value="<b>Держите фокус на WRAPPER</b>"),
    )

    report_id = await wb_api_worker.process_wb_user(
        user_id,
        "token-test",
        wb_client=mock_client,
        app_settings=Settings(wb_api_morning_hour=9, wb_api_morning_minute=0),
    )
    assert report_id is not None

    data = await repo.fetch_table_report_json_for_user(report_id, user_id)
    assert data is not None
    assert "abc_analysis" in data
    assert "out_of_stock_forecast" in data
    assert data["summary"]["group_a_leader"] == "WRAPPER"
    assert data["morning_insight"] == "<b>Держите фокус на WRAPPER</b>"


@pytest.mark.asyncio
async def test_nightly_batch_respects_batch_size_and_pause(wb_db, monkeypatch) -> None:
    for uid in range(1, 8):
        await repo.ensure_user(uid)
        await repo.upsert_wb_api_token(uid, f"tok-{uid}")

    sleep_mock = AsyncMock()
    monkeypatch.setattr(wb_api_worker.asyncio, "sleep", sleep_mock)

    mock_client = MagicMock()
    mock_client.fetch_product_rows = AsyncMock(return_value=[])
    process_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(wb_api_worker, "process_wb_user", process_mock)

    ok, fail = await wb_api_worker.run_nightly_batch(
        wb_client=mock_client,
        app_settings=Settings(),
    )
    assert ok == 7
    assert fail == 0
    assert process_mock.await_count == 7
    # 7 users → 2 batches (5+2) → одна пауза между ними
    assert sleep_mock.await_count == 1
    sleep_mock.assert_awaited_with(wb_api_worker.BATCH_PAUSE_SEC)


@pytest.mark.asyncio
async def test_deliver_morning_notifications(wb_db, monkeypatch) -> None:
    user_id = 100
    await repo.ensure_user(user_id)
    report_id = await repo.insert_table_report(
        user_id,
        json.dumps({"title": "t", "headers": [], "rows": []}),
    )
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    nid = await repo.insert_wb_morning_notification(
        user_id=user_id,
        report_id=report_id,
        scheduled_for=past,
        digest_line="Лидер A: WRAPPER",
        net_profit=10_000,
        group_a_leader="WRAPPER",
        oos_product="BOX",
        oos_days=3,
        fomo_rub=1_500,
        morning_insight="Инсайт",
    )

    notifier = MagicMock()
    notifier.send_morning_analytics = AsyncMock()

    sent = await wb_api_worker.deliver_morning_notifications(notifier)
    assert sent == 1
    notifier.send_morning_analytics.assert_awaited_once()
    call_kwargs = notifier.send_morning_analytics.await_args.kwargs
    assert isinstance(call_kwargs["digest"], WbBatchDigest)
    assert call_kwargs["digest"].group_a_leader == "WRAPPER"
    assert call_kwargs["report_id"] == report_id

    due = await repo.list_due_wb_morning_notifications(datetime.now(timezone.utc).isoformat())
    assert all(row["id"] != nid for row in due)


@pytest.mark.asyncio
async def test_process_wb_user_isolated_failure(wb_db) -> None:
    await repo.ensure_user(1)
    mock_client = MagicMock()
    mock_client.fetch_product_rows = AsyncMock(side_effect=RuntimeError("wb down"))

    report_id = await wb_api_worker.process_wb_user(
        1,
        "bad",
        wb_client=mock_client,
        app_settings=Settings(),
    )
    assert report_id is None


def test_next_morning_scheduled_iso_future_today() -> None:
    iso = wb_api_worker.next_morning_scheduled_iso(hour=9, minute=0, tz=_MSK)
    scheduled = datetime.fromisoformat(iso)
    assert scheduled > datetime.now(timezone.utc) - timedelta(seconds=1)
