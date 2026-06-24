"""Табличные документы: 10 МБ лимит и скачивание на диск (/tmp/)."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import file_processor as fp


def _stub_document(file_id: str, file_size: int | None, file_name: str) -> SimpleNamespace:
    return SimpleNamespace(file_id=file_id, file_size=file_size, file_name=file_name)


def _make_bot(file_path: str, payload: bytes) -> MagicMock:
    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=SimpleNamespace(file_path=file_path))

    async def _download(_tg_path: str, destination) -> None:
        if hasattr(destination, "write"):
            destination.write(payload)
        elif isinstance(destination, (BytesIO,)):
            destination.write(payload)
        else:
            Path(destination).write_bytes(payload)

    bot.download_file = AsyncMock(side_effect=_download)
    return bot


def test_spreadsheet_limit_is_10_mb() -> None:
    assert fp.MAX_SPREADSHEET_BYTES == 10 * 1024 * 1024
    assert fp.max_document_bytes_for("report.xlsx") == fp.MAX_SPREADSHEET_BYTES
    assert fp.max_document_bytes_for("data.csv") == fp.MAX_SPREADSHEET_BYTES
    assert fp.max_document_bytes_for("legacy.xls") == fp.MAX_SPREADSHEET_BYTES
    assert fp.max_document_bytes_for("scan.pdf") == fp.MAX_DOCUMENT_BYTES


def test_spreadsheet_size_ok_boundary() -> None:
    assert fp.is_document_size_ok(fp.MAX_SPREADSHEET_BYTES, file_name="a.xlsx") is True
    assert fp.is_document_size_ok(fp.MAX_SPREADSHEET_BYTES + 1, file_name="a.csv") is False
    assert fp.is_document_size_ok(fp.MAX_DOCUMENT_BYTES, file_name="b.pdf") is True


@pytest.mark.asyncio
async def test_xlsx_downloads_to_path_not_bytesio(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(fp, "_temp_download_dir", lambda: tmp_path)
    bot = _make_bot("docs/sales.xlsx", b"PK\x03\x04fake-xlsx")
    doc = _stub_document("file_xlsx", file_size=100, file_name="sales.xlsx")

    path = await fp.download_telegram_document_to_path(bot, doc)
    try:
        assert Path(path).is_file()
        assert Path(path).read_bytes() == b"PK\x03\x04fake-xlsx"
        assert str(tmp_path) in path.replace("\\", "/") or path.startswith(str(tmp_path))
    finally:
        Path(path).unlink(missing_ok=True)

    bot.get_file.assert_awaited_once_with("file_xlsx")
    bot.download_file.assert_awaited_once()


@pytest.mark.asyncio
async def test_xlsx_over_limit_raises_before_download() -> None:
    bot = _make_bot("docs/big.xlsx", b"never")
    doc = _stub_document(
        "file_big",
        file_size=fp.MAX_SPREADSHEET_BYTES + 1,
        file_name="big.xlsx",
    )
    with pytest.raises(fp.DocumentTooBigError) as excinfo:
        await fp.download_telegram_document_to_path(bot, doc)
    assert excinfo.value.limit_bytes == fp.MAX_SPREADSHEET_BYTES
    bot.get_file.assert_not_called()
    bot.download_file.assert_not_called()


@pytest.mark.asyncio
async def test_buffer_download_rejects_spreadsheet() -> None:
    bot = MagicMock()
    doc = _stub_document("file_csv", file_size=100, file_name="data.csv")
    with pytest.raises(ValueError, match="download_telegram_document_to_path"):
        await fp.download_telegram_document_to_buffer(bot, doc)


@pytest.mark.asyncio
async def test_unknown_xlsx_size_protected_after_download(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(fp, "_temp_download_dir", lambda: tmp_path)
    too_big = b"x" * (fp.MAX_SPREADSHEET_BYTES + 1)
    bot = _make_bot("docs/forwarded.xlsx", too_big)
    doc = _stub_document("file_fwd", file_size=None, file_name="forwarded.xlsx")

    with pytest.raises(fp.DocumentTooBigError) as excinfo:
        await fp.download_telegram_document_to_path(bot, doc)
    assert excinfo.value.size_bytes == fp.MAX_SPREADSHEET_BYTES + 1
    assert excinfo.value.limit_bytes == fp.MAX_SPREADSHEET_BYTES
    leftovers = list(tmp_path.glob("neuromule_sheet_*"))
    assert leftovers == []
