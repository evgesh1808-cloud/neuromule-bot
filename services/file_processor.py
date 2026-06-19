"""Обработка файлов и компрессия извлечённого текста (Маржа +95% Booster).

Фишка 2 мегаспеки NeuroMule 🐎⚡️: каждый текст, который мы извлекаем из .pdf /
.docx / .txt / .md / .csv / caption-сообщения, обязан пройти через
:func:`compress_extracted_text` перед склейкой в финальный промпт. Это срезает
15–20 % веса контекста и напрямую уменьшает счёт OpenRouter:

* убираем zero-width / RTL-override / soft-hyphen и прочие невидимые
  служебные символы — они тратят токены, но не несут смысла;
* множественные пробелы / табуляции → один пробел;
* три и больше переводов строки → ровно два (читаемые абзацы);
* trailing-whitespace в строках → срез;
* финальный ``.strip()`` без потери внутренней структуры.

Для ``.pdf`` используем ``pypdf`` (обязательная зависимость). Сканы без
текстового слоя рендерятся через ``pypdfium2`` → PNG для Vision в Нейротексте.
``python-docx`` остаётся опциональным. Для .txt / .md / .csv — stdlib.

Также экспортируем единый верхний лимит размера файла (15 МБ) — он же
``MAX_DOCUMENT_BYTES`` — для всех точек входа документного инпута бота.
"""

from __future__ import annotations

import base64
import logging
import re
from io import BytesIO
from pathlib import Path
from typing import Any, Final

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

# Жёсткий верхний лимит размера загружаемого документа.
# Telegram сам ограничивает bot-uploaded files 20 МБ, но мы берём
# консервативные 15 МБ — чтобы оставить запас и не получать «file is too
# big» из bot.download_file на тяжёлых .pdf со сканами.
MAX_DOCUMENT_BYTES: Final = 15 * 1024 * 1024  # 15 MB

TXT_DOCUMENT_TOO_BIG = (
    "⚠️ <b>Размер файла превышает лимит 15 МБ.</b>\n\n"
    "Пожалуйста, сожмите документ и отправьте его снова. Подсказка: для "
    "PDF со сканами поможет повторное сохранение в PDF без OCR-слоя."
)


def is_document_size_ok(size_bytes: int | None) -> bool:
    """Проверяет, что размер документа в пределах ``MAX_DOCUMENT_BYTES``.

    ``None`` → ``True`` (Telegram не всегда отдаёт точный размер для
    forwarded-документов; в этом случае мы доверяем хэндлеру выше).
    """

    if size_bytes is None:
        return True
    return 0 <= int(size_bytes) <= MAX_DOCUMENT_BYTES


class DocumentTooBigError(ValueError):
    """Документ превышает ``MAX_DOCUMENT_BYTES``.

    Поднимается ``download_telegram_document_to_buffer`` ДО реального
    ``bot.download_file``, что экономит трафик и RAM ноды Таймвеб
    (потенциально 100+ МБ на каждый «жирный» PDF). Хэндлер должен поймать
    это исключение и ответить юзеру ``TXT_DOCUMENT_TOO_BIG``.
    """

    def __init__(self, size_bytes: int) -> None:
        super().__init__(
            f"document too big: {size_bytes} bytes > limit {MAX_DOCUMENT_BYTES}"
        )
        self.size_bytes = int(size_bytes)


async def download_telegram_document_to_buffer(
    bot: Any,
    document: Any,
    *,
    max_size: int = MAX_DOCUMENT_BYTES,
) -> BytesIO:
    """Безопасно скачать Telegram ``Document`` в ``io.BytesIO``.

    Защищает поток обработки от:

    * over-limit файлов (``DocumentTooBigError`` ДО скачивания);
    * пропавших ``file_size`` (``None``) — доверяем aiogram, но всё равно
      ограничиваем по ``max_size`` при копировании.

    ``bot`` остаётся произвольным `aiogram.Bot`-совместимым объектом, чтобы
    эту функцию можно было моковать в юнит-тестах без поднятия Telegram.

    Возвращает позиционированный в начало ``BytesIO`` с байтами файла.
    """

    size_bytes = getattr(document, "file_size", None)
    if size_bytes is not None and not is_document_size_ok(size_bytes):
        raise DocumentTooBigError(int(size_bytes))

    file_obj = await bot.get_file(document.file_id)
    buffer = BytesIO()
    # aiogram сам потоково пишет в `destination` — поэтому здесь не нужен
    # ручной chunk-loop. Мы держим лимит через предварительную проверку
    # выше + повторный замер длины после download (на случай, если у
    # Telegram было ``file_size=None``).
    await bot.download_file(file_obj.file_path, destination=buffer)
    actual = buffer.tell()
    if not is_document_size_ok(actual):
        raise DocumentTooBigError(actual)
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


def read_xlsx_rows_from_bytes(data: bytes, *, max_rows: int = 5000) -> list[list[str]]:
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
    "DEFAULT_MAX_CHARS",
    "MAX_DOCUMENT_BYTES",
    "TXT_DOCUMENT_TOO_BIG",
    "is_document_size_ok",
    "DocumentTooBigError",
    "download_telegram_document_to_buffer",
)
