"""Ядро мультиплатформенного бота-саммаризатора (OpenAI gpt-4o-mini)."""

from core.summarizer import (
    SummarizeResult,
    chunk_text,
    extract_file_text,
    resolve_raw_text,
    summarize_text,
)

__all__ = [
    "SummarizeResult",
    "chunk_text",
    "extract_file_text",
    "resolve_raw_text",
    "summarize_text",
]
