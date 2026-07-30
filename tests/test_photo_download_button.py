"""Тесты кнопки «Скачать без сжатия» (file_id из Telegram-кэша)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from content import messages as msg
from platforms.handlers.photo_download import cb_download_uncompressed
from services import last_share_media
from services.photo_dl_callback import (
    build_dl_file_callback,
    reset_dl_tokens_for_tests,
    resolve_dl_file_id,
)


def test_build_embeds_short_file_id() -> None:
    cb = build_dl_file_callback(file_id="AgACAgIAshort01", task_id="t1", user_id=1)
    assert cb == f"{msg.CB_DL_FILE_PREFIX}f:AgACAgIAshort01"


def test_build_falls_back_to_task_id_when_file_id_too_long() -> None:
    long_id = "A" * 80
    cb = build_dl_file_callback(file_id=long_id, task_id="ph_abc", user_id=7)
    assert cb == f"{msg.CB_DL_FILE_PREFIX}t:ph_abc"
    assert len(cb.encode("utf-8")) <= 64


def test_resolve_from_share_cache_checks_owner() -> None:
    last_share_media.clear(42)
    last_share_media.remember(
        user_id=42,
        task_id="ph_own",
        task_type="photo",
        prompt="x",
        file_id="fid_owner",
    )
    assert resolve_dl_file_id("t:ph_own", user_id=42) == "fid_owner"
    assert resolve_dl_file_id("t:ph_own", user_id=99) is None
    last_share_media.clear(42)


@pytest.mark.asyncio
async def test_handler_sends_document_and_toasts() -> None:
    reset_dl_tokens_for_tests()
    last_share_media.clear(5)
    last_share_media.remember(
        user_id=5,
        task_id="ph_h1",
        task_type="photo",
        prompt="p",
        file_id="tg_doc_fid",
    )

    message = SimpleNamespace(answer_document=AsyncMock())
    callback = SimpleNamespace(
        data=f"{msg.CB_DL_FILE_PREFIX}t:ph_h1",
        from_user=SimpleNamespace(id=5),
        message=message,
        answer=AsyncMock(),
    )

    await cb_download_uncompressed(callback)  # type: ignore[arg-type]

    message.answer_document.assert_awaited_once()
    assert message.answer_document.await_args.kwargs["document"] == "tg_doc_fid"
    callback.answer.assert_awaited_once_with(msg.TXT_DOWNLOAD_UNCOMPRESSED_OK)
    last_share_media.clear(5)


@pytest.mark.asyncio
async def test_handler_gone_when_cache_miss() -> None:
    callback = SimpleNamespace(
        data=f"{msg.CB_DL_FILE_PREFIX}t:missing_task",
        from_user=SimpleNamespace(id=1),
        message=SimpleNamespace(answer_document=AsyncMock()),
        answer=AsyncMock(),
    )
    await cb_download_uncompressed(callback)  # type: ignore[arg-type]
    callback.message.answer_document.assert_not_awaited()
    callback.answer.assert_awaited_once_with(
        msg.TXT_DOWNLOAD_UNCOMPRESSED_GONE,
        show_alert=True,
    )
