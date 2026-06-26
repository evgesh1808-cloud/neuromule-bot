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
import math
import os
import re
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, Final

from services.table_number_parse import safe_float

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


# ─── Локальный ETL товарной матрицы (ABC / FOMO логистики / OOS) ───────────

_TOP_A_SHARE = 0.20
_OOS_RISK_DAYS = 7
_DEFAULT_REVERSE_LOGISTICS_RUB = 50.0
_RETURN_LOGISTICS_COL_HINTS: tuple[tuple[str, ...], ...] = (
    ("обратн", "логистик"),
    ("обратн", "перевоз"),
    ("обратн", "доставк"),
    ("логистик", "возврат"),
    ("перевоз", "возврат"),
    ("издерж", "возврат"),
)
_RETURN_LOGISTICS_PHRASES: tuple[str, ...] = (
    "обратная логистика",
    "логистика возврат",
    "логистика по возврат",
)
_WB_LABEL_HINTS = ("предмет", "артикул", "наименование", "номенклатур", "бренд")
_WB_NAME_HINTS = ("предмет", "наименование", "номенклатур", "бренд", "товар")
_WB_ARTICLE_HINTS = ("артикул", "sku", "nm id", "nmid", "vendor", "код товара", "barcode", "штрих")
_WB_REVENUE_HINTS = ("перечислению", "выруч", "заработок")
_WB_SALES_HINTS = ("выкупили", "реализован", "продаж")
_WB_DELIVERY_HINTS = ("доставк", "к клиенту")
_WB_RETURN_HINTS = ("возврат",)
_WB_RETURN_ID_SKIP = (
    "srid",
    "rrid",
    "rrd",
    "id ",
    " id",
    "номер",
    "код возврата",
    "документ",
    "транзак",
    "barcode",
    "штрих",
)


def _is_return_id_column(header: str) -> bool:
    """Колонки-ID транзакций не считаем количеством возвратов в штуках."""
    low = (header or "").lower()
    if any(q in low for q in _WB_QTY_UNIT_HINTS):
        return False
    return any(s in low for s in _WB_RETURN_ID_SKIP)
_WB_COMMISSION_HINTS = ("вознагражден", "комисс")
_WB_LOGISTICS_HINTS = ("логистик", "доставк", "хранен")
_WB_AD_HINTS = ("продвижен", "реклам", "удержан")
_WB_STOCK_HINTS = ("остаток", "склад", "stock", "quantity")
_WB_QTY_UNIT_HINTS = ("шт", "кол-во", "количество", "единиц")


@dataclass(frozen=True)
class MatrixSkuDetail:
    """Товарная строка ETL: имя, артикул, выручка, маржа, выкуп."""

    name: str
    article_id: str
    revenue: float
    net_profit: float
    buyout_pct: float
    abc_group: str | None = None
    sales_qty: float = 0.0
    stock_qty: float = 0.0
    unit_cost_rub: float = 0.0

    @property
    def label(self) -> str:
        """Краткое имя для обратной совместимости."""
        return self.name

    def catalog_line(self) -> str:
        """Формат для промпта: Имя (Артикул) — Выручка — Маржа — Выкуп."""
        rev = f"{self.revenue:,.2f}".replace(",", " ")
        margin = f"{self.net_profit:,.2f}".replace(",", " ")
        return (
            f"{self.name} (Артикул: {self.article_id}) — "
            f"{rev} руб. — {margin} руб. — {self.buyout_pct:.1f}%"
        )


@dataclass(frozen=True)
class MatrixAbcSku:
    name: str
    article_id: str
    revenue: float
    net_profit: float
    buyout_pct: float
    abc_group: str

    @property
    def label(self) -> str:
        return self.name


@dataclass(frozen=True)
class MatrixOosForecast:
    label: str
    stock_qty: float
    sales_period_qty: float
    days_until_stockout: float | None
    risk_out_of_stock: bool


@dataclass(frozen=True)
class SellerMatrixEtl:
    """Результат локального ETL по строкам отчёта маркетплейса (0 ₽ OpenRouter)."""

    abc_group_a: tuple[MatrixAbcSku, ...]
    abc_group_c: tuple[MatrixAbcSku, ...]
    abc_a_leader: str
    logistics_fomo_rub: float
    logistics_fomo_detail: str
    oos_forecasts: tuple[MatrixOosForecast, ...]
    oos_critical_sku: str | None
    oos_critical_days: float | None
    logistics_fomo_items: tuple[str, ...] = ()
    reverse_logistics_shop_avg: float = 0.0
    return_logistics_block: str = ""
    sku_catalog: tuple[MatrixSkuDetail, ...] = ()
    outsider_sku: MatrixSkuDetail | None = None


def _matrix_col(headers: list[str], hints: tuple[str, ...], *, require_qty: bool = False) -> int | None:
    for idx, header in enumerate(headers):
        low = (header or "").lower()
        if not any(h in low for h in hints):
            continue
        if require_qty and not any(q in low for q in _WB_QTY_UNIT_HINTS):
            continue
        if any(h in low for h in _WB_RETURN_HINTS) and _is_return_id_column(header):
            continue
        return idx
    return None


def _is_total_row(label: str) -> bool:
    low = (label or "").strip().lower()
    return low.startswith(("итого", "всего", "total"))


def _matrix_name_and_article_cols(headers: list[str]) -> tuple[int, int | None]:
    """Колонка названия товара и опционально отдельный артикул."""
    article_col = _matrix_col(headers, _WB_ARTICLE_HINTS)
    name_col: int | None = None
    for hints in (_WB_NAME_HINTS, _WB_LABEL_HINTS):
        for idx, header in enumerate(headers):
            low = (header or "").lower()
            if any(h in low for h in hints) and idx != article_col:
                name_col = idx
                break
        if name_col is not None:
            break
    if name_col is None:
        name_col = _matrix_col(headers, _WB_LABEL_HINTS) or 0
    if article_col is not None and article_col == name_col:
        article_col = None
    return name_col, article_col


def _row_sku_identity(
    row: list[str],
    *,
    name_col: int,
    article_col: int | None,
) -> tuple[str, str]:
    name = (row[name_col] if name_col < len(row) else "").strip() or "—"
    if article_col is not None and article_col < len(row):
        article = (row[article_col] or "").strip() or name
    else:
        article = name
    return name[:64], article[:48]


@dataclass
class _SkuBucket:
    name: str
    article_id: str
    revenue: float = 0.0
    commission: float = 0.0
    logistics: float = 0.0
    ad_cost: float = 0.0
    extra_cost: float = 0.0
    cost_rub: float = 0.0
    sales_qty: float = 0.0
    deliveries_qty: float = 0.0
    returns_qty: float = 0.0
    stock_qty: float = 0.0
    return_logistics_rub: float = 0.0

    @property
    def unit_cost_rub(self) -> float:
        if self.cost_rub <= 0:
            return 0.0
        if self.sales_qty > 0:
            return self.cost_rub / self.sales_qty
        if self.stock_qty > 0:
            return self.cost_rub / self.stock_qty
        return self.cost_rub

    @property
    def net_profit(self) -> float:
        return self.revenue - self.commission - self.logistics - self.ad_cost - self.extra_cost

    @property
    def unit_logistics(self) -> float:
        if self.sales_qty > 0:
            return self.logistics / self.sales_qty
        if self.deliveries_qty > 0:
            return self.logistics / self.deliveries_qty
        return 0.0

    @property
    def buyout_pct(self) -> float:
        if self.deliveries_qty > 0:
            return self.sales_qty / self.deliveries_qty * 100.0
        if self.sales_qty > 0:
            return 100.0
        return 0.0

    def to_detail(self, *, abc_group: str | None = None) -> MatrixSkuDetail:
        return MatrixSkuDetail(
            name=self.name,
            article_id=self.article_id,
            revenue=round(self.revenue, 2),
            net_profit=round(self.net_profit, 2),
            buyout_pct=round(self.buyout_pct, 1),
            abc_group=abc_group,
            sales_qty=round(self.sales_qty, 2),
            stock_qty=round(self.stock_qty, 2),
            unit_cost_rub=round(self.unit_cost_rub, 2),
        )


def _matrix_return_logistics_cols(headers: list[str]) -> list[int]:
    """Колонки затрат именно на обратную логистику / возвраты (WB xlsx)."""
    cols: list[int] = []
    for idx, header in enumerate(headers):
        low = (header or "").lower()
        if any(phrase in low for phrase in _RETURN_LOGISTICS_PHRASES):
            cols.append(idx)
            continue
        if all(any(h in low for h in group) for group in _RETURN_LOGISTICS_COL_HINTS):
            cols.append(idx)
    return cols


def _matrix_forward_logistics_col(
    headers: list[str],
    return_log_cols: list[int],
) -> int | None:
    """Общая колонка логистики без признаков возврата."""
    for idx, header in enumerate(headers):
        if idx in return_log_cols:
            continue
        low = (header or "").lower()
        if any(h in low for h in ("логистик", "доставк", "хранен", "перевоз")):
            if "возврат" not in low and "обратн" not in low:
                return idx
    return _matrix_col(headers, _WB_LOGISTICS_HINTS)


def _validated_sku_returns_qty(bucket: _SkuBucket) -> float:
    """Фактические возвраты в штуках: не больше доставок/заказов по SKU."""
    raw = max(0.0, bucket.returns_qty)
    if raw <= 0:
        return 0.0
    upper = bucket.deliveries_qty if bucket.deliveries_qty > 0 else 0.0
    if bucket.sales_qty > 0:
        if upper <= 0:
            upper = bucket.sales_qty
        raw = min(raw, upper)
    elif upper > 0:
        raw = min(raw, upper)
    return raw


def _effective_reverse_logistics_qty(bucket: _SkuBucket) -> float:
    """
    Штуки для расчёта обратной логистики: приоритет — колонка «Возвраты, шт.»,
    иначе невыкупы (доставки − выкупы) с тем же потолком по доставкам.
    """
    returns = _validated_sku_returns_qty(bucket)
    if returns > 0:
        return returns
    if bucket.deliveries_qty <= 0:
        return 0.0
    non_buyout = max(0.0, bucket.deliveries_qty - bucket.sales_qty)
    return min(non_buyout, bucket.deliveries_qty)


def _non_buyout_qty(bucket: _SkuBucket) -> float:
    """Обратная совместимость: см. :func:`_effective_reverse_logistics_qty`."""
    return _effective_reverse_logistics_qty(bucket)


def _reverse_logistics_unit_rub(bucket: _SkuBucket, qty: float) -> float:
    """
    Средняя стоимость одной обратной логистики для SKU (руб./ед.).

    Приоритет: колонка «логистика возвратов» / факт из отчёта → доля общей логистики.
    Оценочный тариф не ниже базового WB (~50 ₽/шт).
    """
    if qty <= 0:
        return 0.0
    if bucket.return_logistics_rub > 0:
        return max(_DEFAULT_REVERSE_LOGISTICS_RUB, bucket.return_logistics_rub / qty)
    if bucket.logistics > 0:
        if bucket.deliveries_qty > 0:
            return_share = min(1.0, qty / bucket.deliveries_qty)
            unit = (bucket.logistics * return_share) / qty
        else:
            unit = bucket.logistics / qty
        return max(_DEFAULT_REVERSE_LOGISTICS_RUB, unit)
    return _DEFAULT_REVERSE_LOGISTICS_RUB


def _format_return_logistics_fomo_line(
    name: str,
    article_id: str,
    *,
    returns_count: float,
    total_loss_rub: float,
) -> str:
    label = f"{name[:28]} ({article_id[:16]})" if article_id and article_id != name else name[:40]
    loss_s = f"{total_loss_rub:,.2f}".replace(",", " ")
    return (
        f"Логистика возвратов: {label}: {returns_count:.0f} возвратов. "
        f"Общий убыток на пустых покатушках: ≈ {loss_s} руб."
    )


def compute_seller_matrix_etl(
    rows: list[list[str]],
    *,
    revenue_total: float = 0.0,
    platform: str | None = None,
) -> SellerMatrixEtl | None:
    """
    ABC по чистой прибыли SKU, FOMO логистики невыкупов, прогноз OOS.

    ``rows`` — матрица ``[headers, *data]`` из xlsx/csv (см. :func:`read_xlsx_rows_from_path`).
    ``platform`` — wildberries | ozon | yandex | 1c (формула P&L площадки).
    """
    from services.marketplace_platform import get_marketplace_profile, normalize_marketplace_platform

    if not rows or len(rows) < 2:
        return None

    profile = get_marketplace_profile(platform)
    platform_id = normalize_marketplace_platform(platform)

    headers = [str(h).strip() for h in rows[0]]
    name_col, article_col = _matrix_name_and_article_cols(headers)
    rev_col = _matrix_col(headers, profile.revenue_hints)
    sales_col = _matrix_col(headers, profile.sales_hints, require_qty=True)
    if sales_col is None:
        sales_col = _matrix_col(headers, profile.sales_hints)
    del_col = _matrix_col(headers, profile.delivery_hints, require_qty=True)
    if del_col is None:
        del_col = _matrix_col(headers, profile.delivery_hints)
    ret_col = _matrix_col(headers, profile.return_hints, require_qty=True)
    if ret_col is None:
        ret_col = _matrix_col(headers, profile.return_hints)
    comm_col = _matrix_col(headers, profile.commission_hints)
    return_log_cols = _matrix_return_logistics_cols(headers)
    log_col = _matrix_forward_logistics_col(headers, return_log_cols)
    if log_col is None:
        log_col = _matrix_col(headers, profile.logistics_hints)
    ad_cols = [
        idx
        for idx, h in enumerate(headers)
        if any(x in (h or "").lower() for x in profile.ad_hints)
    ]
    extra_cols = [
        idx
        for idx, h in enumerate(headers)
        if any(x in (h or "").lower() for x in profile.extra_deduction_hints)
        and idx not in ad_cols
        and idx != comm_col
        and idx != log_col
        and idx != cost_col
    ]
    stock_col = _matrix_col(headers, profile.stock_hints)
    cost_col = _matrix_col(headers, ("себестоим", "закуп", "cost"))

    buckets: dict[tuple[str, str], _SkuBucket] = {}
    for row in rows[1:]:
        name, article_id = _row_sku_identity(row, name_col=name_col, article_col=article_col)
        if _is_total_row(name):
            continue
        bucket = buckets.get((name, article_id))
        if bucket is None:
            bucket = _SkuBucket(name=name, article_id=article_id)
            buckets[(name, article_id)] = bucket
        if rev_col is not None and rev_col < len(row):
            bucket.revenue += safe_float(row[rev_col])
        if sales_col is not None and sales_col < len(row):
            bucket.sales_qty += safe_float(row[sales_col])
        if del_col is not None and del_col < len(row):
            bucket.deliveries_qty += safe_float(row[del_col])
        if ret_col is not None and ret_col < len(row):
            bucket.returns_qty += safe_float(row[ret_col])
        if comm_col is not None and comm_col < len(row):
            bucket.commission += abs(safe_float(row[comm_col]))
        if log_col is not None and log_col < len(row):
            bucket.logistics += abs(safe_float(row[log_col]))
        for rl_col in return_log_cols:
            if rl_col < len(row):
                bucket.return_logistics_rub += abs(safe_float(row[rl_col]))
        for ac in ad_cols:
            if ac < len(row):
                bucket.ad_cost += abs(safe_float(row[ac]))
        for ec in extra_cols:
            if ec < len(row):
                bucket.extra_cost += abs(safe_float(row[ec]))
        if cost_col is not None and cost_col < len(row):
            bucket.cost_rub += abs(safe_float(row[cost_col]))
        if stock_col is not None and stock_col < len(row):
            bucket.stock_qty += max(0.0, safe_float(row[stock_col]))

    if not buckets:
        return None

    # ABC по чистой прибыли (Парето)
    ranked = sorted(buckets.items(), key=lambda x: x[1].net_profit, reverse=True)
    n = len(ranked)
    top_a_n = max(1, math.ceil(n * _TOP_A_SHARE))
    group_a_keys = {key for key, _ in ranked[:top_a_n]}
    group_a = tuple(
        MatrixAbcSku(
            name=b.name,
            article_id=b.article_id,
            revenue=round(b.revenue, 2),
            net_profit=round(b.net_profit, 2),
            buyout_pct=round(b.buyout_pct, 1),
            abc_group="A",
        )
        for key, b in ranked
        if key in group_a_keys
    )
    group_c = tuple(
        MatrixAbcSku(
            name=b.name,
            article_id=b.article_id,
            revenue=round(b.revenue, 2),
            net_profit=round(b.net_profit, 2),
            buyout_pct=round(b.buyout_pct, 1),
            abc_group="C",
        )
        for key, b in buckets.items()
        if b.net_profit <= 0
    )
    abc_leader = group_a[0].name if group_a else (ranked[0][1].name if ranked else "—")

    sku_catalog = tuple(
        b.to_detail(
            abc_group=(
                "A"
                if key in group_a_keys
                else ("C" if b.net_profit <= 0 else "B")
            )
        )
        for key, b in ranked
    )
    outsider_sku: MatrixSkuDetail | None = None
    if group_c:
        worst_c = min(group_c, key=lambda s: s.net_profit)
        outsider_sku = MatrixSkuDetail(
            name=worst_c.name,
            article_id=worst_c.article_id,
            revenue=worst_c.revenue,
            net_profit=worst_c.net_profit,
            buyout_pct=worst_c.buyout_pct,
            abc_group="C",
        )

    # FOMO: обратная логистика невыкупов — реальная средняя стоимость из xlsx
    logistics_fomo = 0.0
    fomo_parts: list[str] = []
    weighted_unit_sum = 0.0
    weighted_unit_qty = 0.0
    for (name, _), b in buckets.items():
        returns_qty = _effective_reverse_logistics_qty(b)
        if returns_qty <= 0:
            continue
        unit_log = _reverse_logistics_unit_rub(b, returns_qty)
        loss = returns_qty * unit_log
        if loss > 0:
            logistics_fomo += loss
            weighted_unit_sum += unit_log * returns_qty
            weighted_unit_qty += returns_qty
            if len(fomo_parts) < 6:
                fomo_parts.append(
                    _format_return_logistics_fomo_line(
                        name,
                        b.article_id,
                        returns_count=returns_qty,
                        total_loss_rub=loss,
                    )
                )
    reverse_logistics_shop_avg = (
        weighted_unit_sum / weighted_unit_qty if weighted_unit_qty > 0 else 0.0
    )
    return_logistics_block = (
        "\n".join(f"• {line}" for line in fomo_parts)
        if fomo_parts
        else "• существенных потерь на обратной логистике не выявлено"
    )
    logistics_detail = (
        "Логистика возвратов: " + "; ".join(fomo_parts)
        if fomo_parts
        else "Существенных потерь на обратной логистике не выявлено."
    )

    # OOS: остаток / (продажи за период / 7 дней)
    oos_list: list[MatrixOosForecast] = []
    for (name, _), b in buckets.items():
        daily = b.sales_qty / 7.0 if b.sales_qty > 0 else 0.0
        days: float | None = None
        risk = False
        if daily > 0 and b.stock_qty > 0:
            days = b.stock_qty / daily
            risk = days < _OOS_RISK_DAYS
        elif b.stock_qty <= 0 and b.sales_qty > 0:
            days = 0.0
            risk = True
        oos_list.append(
            MatrixOosForecast(
                label=name,
                stock_qty=b.stock_qty,
                sales_period_qty=b.sales_qty,
                days_until_stockout=round(days, 1) if days is not None else None,
                risk_out_of_stock=risk,
            )
        )
    risky = [f for f in oos_list if f.risk_out_of_stock and f.days_until_stockout is not None]
    risky.sort(key=lambda x: x.days_until_stockout or 999.0)
    oos_sku: str | None = None
    oos_days: float | None = None
    if risky:
        oos_sku = risky[0].label
        oos_days = risky[0].days_until_stockout

    _ = revenue_total  # зарезервировано для будущей нормализации долей
    return SellerMatrixEtl(
        abc_group_a=group_a,
        abc_group_c=group_c,
        abc_a_leader=abc_leader,
        logistics_fomo_rub=round(logistics_fomo, 2),
        logistics_fomo_detail=logistics_detail,
        logistics_fomo_items=tuple(fomo_parts),
        reverse_logistics_shop_avg=round(reverse_logistics_shop_avg, 2),
        return_logistics_block=return_logistics_block,
        oos_forecasts=tuple(oos_list),
        oos_critical_sku=oos_sku,
        oos_critical_days=oos_days,
        sku_catalog=sku_catalog,
        outsider_sku=outsider_sku,
    )


__all__ = (
    "compress_extracted_text",
    "compute_seller_matrix_etl",
    "extract_text_from_document",
    "extract_text_from_pdf",
    "pdf_first_page_to_data_url",
    "render_pdf_first_page_png",
    "read_xlsx_rows_from_bytes",
    "read_xlsx_rows_from_path",
    "SellerMatrixEtl",
    "MatrixAbcSku",
    "MatrixSkuDetail",
    "MatrixOosForecast",
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
