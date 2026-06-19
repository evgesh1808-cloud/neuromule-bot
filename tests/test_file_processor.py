"""Тесты компрессора текста (Text Compressor) и экстрактора документов."""

from __future__ import annotations

import pytest

from services.file_processor import (
    DEFAULT_MAX_CHARS,
    compress_extracted_text,
    extract_text_from_document,
    extract_text_from_pdf,
)


# ─── compress_extracted_text ───────────────────────────────────────────────


def test_compress_empty_input_returns_empty() -> None:
    assert compress_extracted_text("") == ""
    assert compress_extracted_text("   \n\n\n   ") == ""


def test_compress_strips_invisible_chars() -> None:
    raw = "Привет\u200bмир\ufeff!\u00ad"
    out = compress_extracted_text(raw)
    assert out == "Приветмир!"
    assert "\u200b" not in out
    assert "\ufeff" not in out
    assert "\u00ad" not in out


def test_compress_normalizes_multispace_to_single() -> None:
    raw = "Word1     Word2\t\t\tWord3"
    out = compress_extracted_text(raw)
    assert out == "Word1 Word2 Word3"


def test_compress_collapses_multiple_newlines() -> None:
    raw = "A\n\n\n\n\nB\n\n\n\nC"
    out = compress_extracted_text(raw)
    # \n{3,} → \n\n, поэтому ровно один пустой разделитель между блоками
    assert out == "A\n\nB\n\nC"


def test_compress_handles_crlf_normalization() -> None:
    raw = "Line1\r\nLine2\r\nLine3"
    out = compress_extracted_text(raw)
    assert "\r" not in out
    assert out.splitlines() == ["Line1", "Line2", "Line3"]


def test_compress_strips_trailing_spaces_per_line() -> None:
    raw = "Header   \nBody text   \nFooter"
    out = compress_extracted_text(raw)
    assert out.splitlines() == ["Header", "Body text", "Footer"]


def test_compress_is_idempotent() -> None:
    """Повторный прогон не меняет результат — критично для Маржа-Booster."""
    raw = "Title\n\n\n\nBody  text\u200b\nFooter   "
    once = compress_extracted_text(raw)
    twice = compress_extracted_text(once)
    assert once == twice


def test_compress_reduces_at_least_15_percent_on_dirty_doc() -> None:
    """Контракт спеки: -15…20% веса контекста на реальных «грязных» документах."""
    dirty = (
        "Заголовок\u200b статьи\n"
        + "\n" * 12
        + "Параграф 1.    " * 20
        + "\r\n\r\n\r\n"
        + "Параграф 2.\u00ad\u00ad\u00ad\u00ad" * 30
        + "   \n   \n   \n"
    )
    clean = compress_extracted_text(dirty)
    ratio = len(clean) / max(len(dirty), 1)
    assert ratio <= 0.85, f"compression ratio {ratio:.2f} > 0.85"
    assert clean  # не пусто


# ─── extract_text_from_document ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_text_from_txt_compresses_inline(tmp_path) -> None:
    p = tmp_path / "doc.txt"
    p.write_text(
        "Heading\n\n\n\nBody    text\u200b   \n",
        encoding="utf-8",
    )
    out = await extract_text_from_document(p)
    assert out == "Heading\n\nBody text"


@pytest.mark.asyncio
async def test_extract_text_respects_max_chars(tmp_path) -> None:
    p = tmp_path / "big.txt"
    p.write_text("X" * 10_000, encoding="utf-8")
    out = await extract_text_from_document(p, max_chars=100)
    assert len(out) <= 100


@pytest.mark.asyncio
async def test_extract_text_from_md_and_csv(tmp_path) -> None:
    md = tmp_path / "note.md"
    md.write_text("# Title\n\n\n* a\n* b\n", encoding="utf-8")
    out_md = await extract_text_from_document(md)
    assert "# Title" in out_md
    assert "* a" in out_md and "* b" in out_md

    csv = tmp_path / "data.csv"
    csv.write_text("a;b;c\n1;2;3\n4;5;6\n", encoding="utf-8")
    out_csv = await extract_text_from_document(csv)
    assert out_csv.splitlines() == ["a;b;c", "1;2;3", "4;5;6"]


@pytest.mark.asyncio
async def test_extract_text_unsupported_raises(tmp_path) -> None:
    p = tmp_path / "weird.bin"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        await extract_text_from_document(p)


def test_default_max_chars_is_sane() -> None:
    # ULTRA-лимит 500k по спеке — наш дефолт должен быть согласован.
    assert DEFAULT_MAX_CHARS >= 500_000


def _minimal_text_pdf(text: str = "Hello PDF") -> bytes:
    from io import BytesIO

    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 800, text)
    c.save()
    return buf.getvalue()


def test_extract_text_from_pdf_reads_text_layer() -> None:
    pdf_bytes = _minimal_text_pdf("NeuroMule PDF test")
    out = extract_text_from_pdf(pdf_bytes)
    assert "NeuroMule PDF test" in out


@pytest.mark.asyncio
async def test_extract_text_from_document_pdf(tmp_path) -> None:
    p = tmp_path / "doc.pdf"
    p.write_bytes(_minimal_text_pdf("From path"))
    out = await extract_text_from_document(p)
    assert "From path" in out
