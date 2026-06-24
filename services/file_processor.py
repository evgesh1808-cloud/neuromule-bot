"""Обработка файлов и компрессия извлечённого текста (Маржа +95% Booster).

Фишка 2 мегаспеки NeuroMule 🐎⚡️: каждый текст, который мы извлекаем из .pdf /
.docx / .txt / .md / .csv / caption-сообщения, обязан пройти через
:func:`compress_extracted_text` перед склейкой в финальный промпт.

Для ``.pdf`` используем ``pypdf``. Сканы без текстового слоя рендерятся через
``pypdfium2`` → PNG для Vision в Нейротексте.

Лимиты размера документов:

* ``.xlsx`` / ``.xls`` / ``.csv`` — до **10 МБ**, скачивание чанками на диск
  в ``/tmp/`` (без удержания файла в RAM через ``BytesIO``);
* остальные форматы (``.pdf``, ``.docx``, ``.txt``, …) — до **15 МБ** в буфере.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, Final

logger = logging.getLogger(__name__)

# Видимые-под-микроскопом, но «токеноёмкие» символы: ZW-space, ZW-non-joiner,
# ZW-joiner, LRM / RLM, LRE / RLE / PDF / LRO / RLO, BOM, soft-hyphen, word-joiner.
_INVISIBLE_RE: Final = re.compile(
    r"[\u00ad\u200b-\u200f\u202a-\u202e\u2060\u2061\u2062\u2063\u2064\ufeff]"
)
_CARRIAGE_RE: Final = re.compile(r"\r\n?")
_TRAILING_WS_RE: Final = re.compile(r"[ \t]+(?=\n)")
_MULTI_SPACE_RE: Final = re.compile(r"[ \t]{2,}")
_MULTI_NEWLINE_RE: Final = re.compile(r"\n{3,}")

# Гард, чтобы не уронить ноду на 200-МБ "txt-бомбе".
DEFAULT_MAX_CHARS: Final = 500_000

# Жёсткий верхний лимит для табличных форматов (финансовые отчёты селлеров).
MAX_SPREADSHEET_BYTES: Final = 10 * 1024 * 1024  # 10 MB

# Лимит для прочих документов (.pdf, .docx, .txt, …).
MAX_DOCUMENT_BYTES: Final = 15 * 1024 * 1024  # 15 MB

SPREADSHEET_SUFFIXES: Final = frozenset({".xlsx", ".xls", ".csv"})

_DOWNLOAD_CHUNK_SIZE: Final = 64 * 1024

TXT_SPREADSHEET_TOO_BIG = (
    "⚠️ <b>Размер табличного файла превышает лимит 10 МБ.</b>\n\n"
    "Пожалуйста, сократите отчёт (меньше строк/листов) или отправьте сжатый "
    "файл .xlsx/.csv."
)

TXT_DOCUMENT_TOO_BIG = (
    "⚠️ <b>Размер файла превышает лимит 15 МБ.</b>\n\n"
    "Пожалуйста, сожмите документ и отправьте его снова. Подсказка: для "
    "PDF со сканами поможет повторное сохранение в PDF без OCR-слоя."
)


def _document_suffix(file_name: str | None) -> str:
    return Path((file_name or "document").strip()).suffix.lower()


def is_spreadsheet_suffix(suffix: str) -> bool:
    return (suffix or "").strip().lower() in SPREADSHEET_SUFFIXES


def max_document_bytes_for(file_name: str | None) -> int:
    """Возвращает лимит в байтах в зависимости от расширения файла."""
    if is_spreadsheet_suffix(_document_suffix(file_name)):
        return MAX_SPREADSHEET_BYTES
    return MAX_DOCUMENT_BYTES


def document_too_big_message(file_name: str | None) -> str:
    if is_spreadsheet_suffix(_document_suffix(file_name)):
        return TXT_SPREADSHEET_TOO_BIG
    return TXT_DOCUMENT_TOO_BIG


def _temp_download_dir() -> Path:
    preferred = Path("/tmp")
    if preferred.is_dir():
        return preferred
    return Path(tempfile.gettempdir())


def is_document_size_ok(
    size_bytes: int | None,
    *,
    file_name: str | None = None,
) -> bool:
    """Проверяет размер документа с учётом расширения (10 / 15 МБ).

    ``None`` → ``True`` (Telegram не всегда отдаёт точный размер).
    """

    if size_bytes is None:
        return True
    limit = max_document_bytes_for(file_name)
    return 0 <= int(size_bytes) <= limit


class DocumentTooBigError(ValueError):
    """Документ превышает лимит для своего типа."""

    def __init__(
        self,
        size_bytes: int,
        *,
        file_name: str | None = None,
        limit_bytes: int | None = None,
    ) -> None:
        limit = int(limit_bytes if limit_bytes is not None else max_document_bytes_for(file_name))
        super().__init__(f"document too big: {size_bytes} bytes > limit {limit}")
        self.size_bytes = int(size_bytes)
        self.limit_bytes = limit
        self.file_name = file_name


class _LimitingDiskWriter(BinaryIO):
    """Потоковая запись чанков на диск с жёстким лимитом размера."""

    def __init__(self, path: Path, *, max_bytes: int) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._written = 0
        self._fh = path.open("wb")
        self._closed = False

    def write(self, data: bytes) -> int:  # type: ignore[override]
        if self._closed:
            raise ValueError("writer is closed")
        nbytes = len(data)
        if self._written + nbytes > self._max_bytes:
            self._abort()
            raise DocumentTooBigError(
                self._written + nbytes,
                limit_bytes=self._max_bytes,
            )
        self._fh.write(data)
        self._written += nbytes
        return nbytes

    def _abort(self) -> None:
        self._closed = True
        try:
            self._fh.close()
        finally:
            self._path.unlink(missing_ok=True)

    def close(self) -> None:
        if not self._closed:
            self._fh.close()
            self._closed = True

    @property
    def size(self) -> int:
        return self._written

    def readable(self) -> bool:
        return False

    def writable(self) -> bool:
        return not self._closed

    def seekable(self) -> bool:
        return False

    def flush(self) -> None:
        self._fh.flush()


async def download_telegram_document_to_path(
    bot: Any,
    document: Any,
    *,
    file_name: str | None = None,
) -> str:
    """Скачивает табличный документ чанками в ``/tmp/``, возвращает путь к файлу.

    Только ``.xlsx``, ``.xls``, ``.csv`` — без удержания тела файла в RAM.
    Caller обязан удалить файл после обработки.
    """
    name = (file_name or getattr(document, "file_name", None) or "document").strip()
    suffix = _document_suffix(name)
    if not is_spreadsheet_suffix(suffix):
        raise ValueError(
            f"download_telegram_document_to_path supports spreadsheet files only, got {suffix!r}"
        )

    max_size = max_document_bytes_for(name)
    size_bytes = getattr(document, "file_size", None)
    if size_bytes is not None and not is_document_size_ok(size_bytes, file_name=name):
        raise DocumentTooBigError(int(size_bytes), file_name=name, limit_bytes=max_size)

    file_obj = await bot.get_file(document.file_id)
    tmp_dir = _temp_download_dir()
    fd, tmp_name = tempfile.mkstemp(prefix="neuromule_sheet_", suffix=suffix, dir=str(tmp_dir))
    os.close(fd)
    dest_path = Path(tmp_name)
    writer = _LimitingDiskWriter(dest_path, max_bytes=max_size)
    try:
        await bot.download_file(file_obj.file_path, destination=writer)
        writer.close()
        if not is_document_size_ok(writer.size, file_name=name):
            dest_path.unlink(missing_ok=True)
            raise DocumentTooBigError(writer.size, file_name=name, limit_bytes=max_size)
        return str(dest_path)
    except DocumentTooBigError:
        raise
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise
    finally:
        if not writer._closed:
            writer.close()


async def download_telegram_document_to_buffer(
    bot: Any,
    document: Any,
    *,
    file_name: str | None = None,
    max_size: int | None = None,
) -> BytesIO:
    """Безопасно скачать Telegram ``Document`` в ``io.BytesIO`` (не для таблиц).

    Для ``.xlsx`` / ``.xls`` / ``.csv`` используйте
    :func:`download_telegram_document_to_path`.
    """
    name = (file_name or getattr(document, "file_name", None) or "document").strip()
    suffix = _document_suffix(name)
    if is_spreadsheet_suffix(suffix):
        raise ValueError(
            "spreadsheet documents must use download_telegram_document_to_path, not BytesIO"
        )

    limit = int(max_size if max_size is not None else max_document_bytes_for(name))
    size_bytes = getattr(document, "file_size", None)
    if size_bytes is not None and int(size_bytes) > limit:
        raise DocumentTooBigError(int(size_bytes), file_name=name, limit_bytes=limit)

    file_obj = await bot.get_file(document.file_id)
    buffer = BytesIO()
    await bot.download_file(file_obj.file_path, destination=buffer)
    actual = buffer.tell()
    if actual > limit:
        raise DocumentTooBigError(actual, file_name=name, limit_bytes=limit)
    buffer.seek(0)
    return buffer


def compress_extracted_text(raw_text: str) -> str:
    """Сжимает извлечённый из документа/текста сырой контент.

    Гарантии:

    * Удаляет невидимые / RTL-override / BOM символы.
    * Нормализует переводы строк (``\\r\\n`` → ``\\n``).
    * Стрипает пробелы в конце каждой строки.
    * ``[ \\t]{2,}`` → один пробел внутри строки.
    * ``\\n{3,}`` → ``\\n\\n`` (один пустой разделитель между абзацами).
    * Делает финальный ``strip``.

    Идемпотентна: повторный прогон через себя не меняет результат.
    """

    if not raw_text:
        return ""

    text = _CARRIAGE_RE.sub("\n", raw_text)
    text = _INVISIBLE_RE.sub("", text)
    text = _TRAILING_WS_RE.sub("", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


# ─── Document extractors ───────────────────────────────────────────────────


def _safe_truncate(text: str, max_chars: int) -> str:
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars]
    return text


def _read_txt(path: Path, max_chars: int) -> str:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    return _safe_truncate(raw, max_chars)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Извлекает текстовый слой PDF через ``pypdf`` (пустая строка = скан/картинки)."""
    try:
        import pypdf
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "pypdf не установлен: pip install 'pypdf>=4.0'"
        ) from exc

    reader = pypdf.PdfReader(BytesIO(file_bytes))
    text_layers: list[str] = []
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text_layers.append(page_text)
    return "\n".join(text_layers).strip()


def render_pdf_first_page_png(
    file_bytes: bytes,
    *,
    max_side_px: int = 1600,
) -> bytes | None:
    """Рендерит первую страницу PDF в PNG для Vision-OCR (сканы без текстового слоя)."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        logger.warning("pypdfium2 не установлен — Vision-fallback для PDF недоступен")
        return None

    try:
        pdf = pdfium.PdfDocument(file_bytes)
        if len(pdf) == 0:
            return None
        page = pdf[0]
        width, height = page.get_size()
        scale = min(
            max_side_px / max(width, 1.0),
            max_side_px / max(height, 1.0),
            3.0,
        )
        bitmap = page.render(scale=scale)
        pil_image = bitmap.to_pil()
        buffer = BytesIO()
        pil_image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    except Exception:
        logger.exception("render_pdf_first_page_png failed")
        return None


def pdf_first_page_to_data_url(file_bytes: bytes) -> str | None:
    """Первая страница PDF → ``data:image/png;base64,...`` для OpenRouter Vision."""
    png = render_pdf_first_page_png(file_bytes)
    if not png:
        return None
    encoded = base64.standard_b64encode(png).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _read_pdf(path: Path, max_chars: int) -> str:
    return _safe_truncate(extract_text_from_pdf(path.read_bytes()), max_chars)


def _read_docx(path: Path, max_chars: int) -> str:
    try:
        from docx import Document  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "python-docx не установлен: добавьте 'python-docx>=1.1' в requirements.txt"
        ) from exc

    doc = Document(str(path))
    paragraphs: list[str] = []
    total = 0
    for paragraph in doc.paragraphs:
        text = paragraph.text or ""
        if not text:
            continue
        paragraphs.append(text)
        total += len(text)
        if max_chars and total >= max_chars:
            break
    return _safe_truncate("\n".join(paragraphs), max_chars)


def _read_xlsx_rows(path: Path, *, max_rows: int = 5000) -> list[list[str]]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows: list[list[str]] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= max_rows:
            break
        cells = ["" if v is None else str(v).strip() for v in row]
        if any(cells):
            rows.append(cells)
    wb.close()
    return rows


def read_xlsx_rows_from_path(path: Path | str, *, max_rows: int = 5000) -> list[list[str]]:
    """Читает строки Excel с диска (без загрузки всего файла в ``BytesIO``)."""
    return _read_xlsx_rows(Path(path), max_rows=max_rows)


def read_xlsx_rows_from_bytes(data: bytes, *, max_rows: int = 5000) -> list[list[str]]:
    """Читает Excel из небольшого in-memory буфера (только для тестов / микро-файлов)."""
    from openpyxl import load_workbook

    wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows: list[list[str]] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= max_rows:
            break
        cells = ["" if v is None else str(v).strip() for v in row]
        if any(cells):
            rows.append(cells)
    wb.close()
    return rows


def _read_xlsx(path: Path, max_chars: int) -> str:
    from services.table_markdown import rows_to_markdown_table

    rows = _read_xlsx_rows(path)
    if not rows:
        return ""
    md = rows_to_markdown_table(rows)
    return _safe_truncate(md, max_chars)


async def extract_text_from_document(
    path: Path | str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    compress: bool = True,
) -> str:
    """Универсальный экстрактор: ``.txt/.md/.log/.csv`` + опц. ``.pdf/.docx``.

    Кидает ``ValueError`` для неизвестного расширения и ``RuntimeError`` если
    pypdf/python-docx не установлены. Возвращаемый текст уже пропущен через
    :func:`compress_extracted_text` (если ``compress=True``) — пайплайн чата
    может его склеивать с пользовательским caption без дополнительных шагов.
    """

    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".txt", ".md", ".log", ".csv"):
        raw = _read_txt(p, max_chars)
    elif suffix == ".pdf":
        raw = _read_pdf(p, max_chars)
    elif suffix in (".docx",):
        raw = _read_docx(p, max_chars)
    elif suffix == ".xlsx":
        raw = _read_xlsx(p, max_chars)
    else:
        raise ValueError(f"Unsupported document type: {suffix or '<no-ext>'}")

    if compress:
        return compress_extracted_text(raw)
    return raw


__all__ = (
    "compress_extracted_text",
    "extract_text_from_document",
    "extract_text_from_pdf",
    "pdf_first_page_to_data_url",
    "render_pdf_first_page_png",
    "read_xlsx_rows_from_bytes",
    "read_xlsx_rows_from_path",
    "DEFAULT_MAX_CHARS",
    "MAX_DOCUMENT_BYTES",
    "MAX_SPREADSHEET_BYTES",
    "SPREADSHEET_SUFFIXES",
    "TXT_DOCUMENT_TOO_BIG",
    "TXT_SPREADSHEET_TOO_BIG",
    "document_too_big_message",
    "is_spreadsheet_suffix",
    "max_document_bytes_for",
    "is_document_size_ok",
    "DocumentTooBigError",
    "download_telegram_document_to_buffer",
    "download_telegram_document_to_path",
)
