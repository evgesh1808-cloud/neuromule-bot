"""PR-C: 15 МБ guard на входе документа из Telegram (без блокирующего I/O)."""
from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import file_processor as fp


def _stub_document(file_id: str, file_size: int | None) -> SimpleNamespace:
    return SimpleNamespace(file_id=file_id, file_size=file_size)


def _make_bot(file_path: str, payload: bytes) -> MagicMock:
    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=SimpleNamespace(file_path=file_path))

    async def _download(file_path: str, destination: BytesIO) -> None:
        destination.write(payload)

    bot.download_file = AsyncMock(side_effect=_download)
    return bot


@pytest.mark.asyncio
async def test_small_document_downloads_into_bytesio() -> None:
    bot = _make_bot("docs/x.txt", b"hello world")
    doc = _stub_document("file_abc", file_size=11)
    buf = await fp.download_telegram_document_to_buffer(bot, doc)
    assert isinstance(buf, BytesIO)
    assert buf.tell() == 0  # позиционирован в начало
    assert buf.read() == b"hello world"
    bot.get_file.assert_awaited_once_with("file_abc")
    bot.download_file.assert_awaited_once()


@pytest.mark.asyncio
async def test_over_limit_size_raises_before_download() -> None:
    bot = _make_bot("docs/big.pdf", b"never reached")
    doc = _stub_document("file_big", file_size=fp.MAX_DOCUMENT_BYTES + 1)
    with pytest.raises(fp.DocumentTooBigError) as excinfo:
        await fp.download_telegram_document_to_buffer(bot, doc)
    assert excinfo.value.size_bytes == fp.MAX_DOCUMENT_BYTES + 1
    # get_file / download_file НЕ должны быть вызваны: экономим трафик.
    bot.get_file.assert_not_called()
    bot.download_file.assert_not_called()


@pytest.mark.asyncio
async def test_at_limit_boundary_is_allowed() -> None:
    bot = _make_bot("docs/boundary.pdf", b"x" * 10)
    doc = _stub_document("file_boundary", file_size=fp.MAX_DOCUMENT_BYTES)
    buf = await fp.download_telegram_document_to_buffer(bot, doc)
    assert isinstance(buf, BytesIO)


@pytest.mark.asyncio
async def test_unknown_size_still_protected_by_post_download_check() -> None:
    """Если у Telegram нет file_size — мы всё равно ловим over-limit по факту."""
    too_big_payload = b"x" * (fp.MAX_DOCUMENT_BYTES + 1)
    bot = _make_bot("docs/forwarded.pdf", too_big_payload)
    doc = _stub_document("file_fwd", file_size=None)
    with pytest.raises(fp.DocumentTooBigError) as excinfo:
        await fp.download_telegram_document_to_buffer(bot, doc)
    # На этот раз исключение прилетит ПОСЛЕ download (мы не знали размер заранее).
    assert excinfo.value.size_bytes == fp.MAX_DOCUMENT_BYTES + 1


@pytest.mark.asyncio
async def test_too_big_message_format_is_html_alert() -> None:
    # Sanity: HTML-сообщение есть и упоминает 15 МБ.
    assert "15 МБ" in fp.TXT_DOCUMENT_TOO_BIG
    assert fp.TXT_DOCUMENT_TOO_BIG.startswith("⚠️")
