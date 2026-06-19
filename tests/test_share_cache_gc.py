"""Тесты GC-логики кэша шеринга (``purge_expired`` / TTL 48 ч)."""
from __future__ import annotations

import time

import pytest

from services import last_share_media


@pytest.fixture(autouse=True)
def _reset_share_cache():
    last_share_media._BY_USER.clear()
    last_share_media._BY_TASK.clear()
    last_share_media._TS.clear()
    yield
    last_share_media._BY_USER.clear()
    last_share_media._BY_TASK.clear()
    last_share_media._TS.clear()


def test_purge_keeps_fresh_entries() -> None:
    last_share_media.remember(
        user_id=1,
        task_id="t1",
        task_type="photo",
        prompt="x",
        file_id="ph_1",
    )
    removed = last_share_media.purge_expired(ttl_sec=10.0)
    assert removed == 0
    assert last_share_media.get_by_task("t1") is not None


def test_purge_removes_expired_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    last_share_media.remember(
        user_id=2,
        task_id="t2",
        task_type="video",
        prompt="x",
        file_id="vid_2",
    )
    # Откатываем timestamp на «3 дня назад».
    last_share_media._TS["t2"] = time.monotonic() - 3 * 24 * 3600

    removed = last_share_media.purge_expired(ttl_sec=48 * 3600)
    assert removed == 1
    assert last_share_media.get_by_task("t2") is None
    assert last_share_media.get_by_user(2) is None


def test_purge_does_not_clobber_newer_entry_of_same_user() -> None:
    """Если у юзера была старая запись и появилась свежая — новая остаётся."""
    last_share_media.remember(
        user_id=7, task_id="old", task_type="photo", prompt="o", file_id="o"
    )
    last_share_media._TS["old"] = time.monotonic() - 3 * 24 * 3600
    last_share_media.remember(
        user_id=7, task_id="new", task_type="photo", prompt="n", file_id="n"
    )
    removed = last_share_media.purge_expired(ttl_sec=48 * 3600)
    assert removed == 1
    # Новая всё ещё есть.
    assert last_share_media.get_by_user(7) is not None
    assert last_share_media.get_by_user(7).task_id == "new"
