"""Тесты 15 MB лимита для документного инпута."""
from __future__ import annotations

import pytest

from services import file_processor as fp


def test_default_limit_is_15_mb() -> None:
    assert fp.MAX_DOCUMENT_BYTES == 15 * 1024 * 1024
    assert fp.MAX_SPREADSHEET_BYTES == 10 * 1024 * 1024


def test_size_ok_for_small_file() -> None:
    assert fp.is_document_size_ok(1024 * 1024) is True
    assert fp.is_document_size_ok(9 * 1024 * 1024, file_name="a.xlsx") is True


def test_size_ok_at_exact_boundary() -> None:
    assert fp.is_document_size_ok(fp.MAX_DOCUMENT_BYTES, file_name="doc.pdf") is True
    assert fp.is_document_size_ok(fp.MAX_SPREADSHEET_BYTES, file_name="a.csv") is True


def test_size_not_ok_one_byte_over() -> None:
    assert fp.is_document_size_ok(fp.MAX_DOCUMENT_BYTES + 1, file_name="doc.pdf") is False
    assert fp.is_document_size_ok(fp.MAX_SPREADSHEET_BYTES + 1, file_name="a.xlsx") is False


def test_size_none_is_permissive() -> None:
    """Если Telegram не отдал size — доверяем хэндлеру выше."""
    assert fp.is_document_size_ok(None) is True


def test_too_big_message_is_html_and_mentions_limits() -> None:
    assert "15 МБ" in fp.TXT_DOCUMENT_TOO_BIG
    assert "10 МБ" in fp.TXT_SPREADSHEET_TOO_BIG
    assert "<b>" in fp.TXT_DOCUMENT_TOO_BIG and "</b>" in fp.TXT_DOCUMENT_TOO_BIG
    assert fp.document_too_big_message("a.xlsx") == fp.TXT_SPREADSHEET_TOO_BIG
    assert fp.document_too_big_message("scan.pdf") == fp.TXT_DOCUMENT_TOO_BIG
