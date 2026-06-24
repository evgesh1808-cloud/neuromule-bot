"""Скачивание фото/документов из Telegram и сборка payload для OpenRouter."""

from __future__ import annotations

import base64
import logging
import os
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_NEUROTEXT_PHOTO_BYTES = 5 * 1024 * 1024  # 5 MB

NEUROTEXT_DOCUMENT_SUFFIXES: frozenset[str] = frozenset(
    {".txt", ".csv", ".pdf", ".docx"}
)

PDF_SCAN_VISION_PROMPT = (
    "На изображении — первая страница PDF-документа (скан или страница без "
    "текстового слоя). Распознай и извлеки весь видимый текст, сохрани "
    "структуру абзацев. Если на странице таблица — верни её в Markdown."
)


@dataclass(frozen=True)
class NeurotextDocumentPayload:
    """Результат чтения документа: текст и/или Vision data-URL для скан-PDF."""

    extracted_text: str
    scan_image_data_url: str | None = None

    @property
    def needs_vision(self) -> bool:
        return bool(self.scan_image_data_url)


class PdfScanUnreadableError(RuntimeError):
    """PDF без текстового слоя и не удалось отрендерить страницу для Vision."""


class NeurotextPhotoTooBigError(ValueError):
    def __init__(self, size_bytes: int) -> None:
        super().__init__(f"photo too big: {size_bytes} bytes")
        self.size_bytes = int(size_bytes)


class NeurotextUnsupportedDocumentError(ValueError):
    def __init__(self, suffix: str) -> None:
        super().__init__(f"unsupported document suffix: {suffix}")
        self.suffix = suffix


def _mime_from_file_path(file_path: str | None) -> str:
    path = (file_path or "").lower()
    if path.endswith(".png"):
        return "image/png"
    if path.endswith(".webp"):
        return "image/webp"
    if path.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


async def telegram_photo_to_data_url(bot: Any, photo: Any) -> str:
    """Скачивает ``PhotoSize`` через сессию бота (с прокси) → data-URL для OpenRouter."""
    size_bytes = getattr(photo, "file_size", None)
    if size_bytes is not None and int(size_bytes) > MAX_NEUROTEXT_PHOTO_BYTES:
        raise NeurotextPhotoTooBigError(int(size_bytes))

    file_obj = await bot.get_file(photo.file_id)
    if not file_obj or not file_obj.file_path:
        raise RuntimeError("Telegram did not return file_path for photo")

    buffer = BytesIO()
    await bot.download_file(file_obj.file_path, destination=buffer)
    raw = buffer.getvalue()
    if len(raw) > MAX_NEUROTEXT_PHOTO_BYTES:
        raise NeurotextPhotoTooBigError(len(raw))
    if not raw:
        raise RuntimeError("empty photo payload from Telegram")

    mime = _mime_from_file_path(file_obj.file_path)
    encoded = base64.standard_b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


async def telegram_document_to_neurotext_payload(
    bot: Any,
    document: Any,
    *,
    max_chars: int,
) -> NeurotextDocumentPayload:
    """Скачивает документ: текст через ``file_processor`` или Vision для скан-PDF."""
    from services.file_processor import (
        compress_extracted_text,
        download_telegram_document_to_buffer,
        download_telegram_document_to_path,
        extract_text_from_document,
        extract_text_from_pdf,
        is_spreadsheet_suffix,
        pdf_first_page_to_data_url,
    )

    file_name = (getattr(document, "file_name", None) or "").strip()
    suffix = Path(file_name).suffix.lower()
    if suffix not in NEUROTEXT_DOCUMENT_SUFFIXES:
        raise NeurotextUnsupportedDocumentError(suffix or "<no-ext>")

    if is_spreadsheet_suffix(suffix):
        file_path = await download_telegram_document_to_path(
            bot,
            document,
            file_name=file_name,
        )
        try:
            extracted = await extract_text_from_document(
                file_path,
                max_chars=max_chars,
                compress=True,
            )
            return NeurotextDocumentPayload(extracted_text=extracted)
        finally:
            Path(file_path).unlink(missing_ok=True)

    buffer = await download_telegram_document_to_buffer(
        bot,
        document,
        file_name=file_name,
    )
    raw_bytes = buffer.getvalue()

    if suffix == ".pdf":
        extracted = compress_extracted_text(extract_text_from_pdf(raw_bytes))
        if max_chars > 0 and len(extracted) > max_chars:
            extracted = extracted[:max_chars]
        if extracted.strip():
            return NeurotextDocumentPayload(extracted_text=extracted)
        data_url = pdf_first_page_to_data_url(raw_bytes)
        if not data_url:
            raise PdfScanUnreadableError(
                "pdf scan: empty text layer and page render failed"
            )
        return NeurotextDocumentPayload(
            extracted_text="",
            scan_image_data_url=data_url,
        )

    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(raw_bytes)
        extracted = await extract_text_from_document(
            tmp_path,
            max_chars=max_chars,
            compress=True,
        )
        return NeurotextDocumentPayload(extracted_text=extracted)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


async def telegram_document_to_prompt_text(
    bot: Any,
    document: Any,
    *,
    max_chars: int,
) -> str:
    """Скачивает Telegram ``Document`` и извлекает текст через ``file_processor``."""
    payload = await telegram_document_to_neurotext_payload(
        bot,
        document,
        max_chars=max_chars,
    )
    if payload.needs_vision:
        raise PdfScanUnreadableError(
            "pdf scan requires vision path; use telegram_document_to_neurotext_payload"
        )
    return payload.extracted_text


def merge_document_caption_and_text(caption: str, extracted: str) -> str:
    """Склеивает подпись к файлу и извлечённый текст в один промпт."""
    cap = (caption or "").strip()
    body = (extracted or "").strip()
    if cap and body:
        return f"{cap}\n\n{body}"
    return cap or body


def build_openrouter_user_content(
    text: str,
    *,
    image_data_url: str | None = None,
) -> str | list[dict[str, Any]]:
    """Текст или multimodal-массив ``content`` для OpenRouter (Gemini Flash vision)."""
    prompt = (text or "").strip()
    if not image_data_url:
        return prompt
    parts: list[dict[str, Any]] = []
    if prompt:
        parts.append({"type": "text", "text": prompt})
    parts.append({"type": "image_url", "image_url": {"url": image_data_url}})
    return parts
