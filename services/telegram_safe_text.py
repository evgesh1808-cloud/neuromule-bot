"""Безопасный plain-text для Telegram (без parse_mode)."""

from __future__ import annotations

import re

from services.table_markdown import (
    is_markdown_table_row as _is_markdown_table_row,
    is_markdown_table_separator as _is_markdown_table_separator,
    parse_markdown_table_row as _parse_markdown_table_row,
)

_TAG_RE = re.compile(r"<[^>]+>")
_BROKEN_ENTITY_RE = re.compile(r"&(#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);")
_BULLET_LEAD_RE = re.compile(r"^(?:[•·▪\*]\s*)+|^-\s+")
_HTML_TAG_RE = re.compile(
    r"</?(b|strong|i|em|u|ins|s|strike|del|code|pre|a|blockquote|span|tg-spoiler)"
    r"(?:\s[^>]*)?>",
    re.IGNORECASE,
)

_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")  # noqa: F401 — re-export compat


_LIST_MARKER_RE = re.compile(r"^[•\*]\s+|^-\s+")
_NUMBERED_ITEM_RE = re.compile(r"^\d+\.\s+")


def _line_plain_text(line: str) -> str:
    return _TAG_RE.sub("", line).strip()


def _is_list_section_header_line(line: str) -> bool:
    """Заголовок блока («Ключевые тезисы:») — без маркера списка."""
    plain = _line_plain_text(line)
    if not plain.endswith(":"):
        return False
    return len(plain.split()) <= 15


def _strip_leading_indent(line: str) -> str:
    """Убирает пробелы/табы в начале строки — верстка от левого края."""
    if not line.strip():
        return ""
    return line.lstrip(" \t")


def markdown_to_html(text: str) -> str:
    """Markdown → HTML Telegram: жирный/курсив, заголовки без #, без принудительных маркеров списка."""
    if not text:
        return ""
    text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.*?)\*", r"<i>\1</i>", text)
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _strip_leading_indent(line)
        if not line:
            lines.append("")
            continue
        if _NUMBERED_ITEM_RE.match(line):
            lines.append(line)
            continue
        if not _LIST_MARKER_RE.match(line):
            lines.append(line)
            continue
        body = _LIST_MARKER_RE.sub("", line).strip()
        lines.append(body)
    return "\n".join(lines)


def _plain_line(line: str) -> str:
    return _TAG_RE.sub("", line).strip()


def _is_list_section_header(body: str) -> bool:
    plain = _plain_line(body)
    if not plain.endswith(":"):
        return False
    return len(plain.split()) <= 15


def collapse_excessive_line_breaks(text: str) -> str:
    """Плотная верстка: не более одного \\n подряд (без двойных пустых строк)."""
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{2,}", "\n", normalized)


def normalize_telegram_list_markup(text: str) -> str:
    """
    Постобработка верстки: снимает отступы слева и устаревшие маркеры •/-/*,
    сохраняет нумерацию 1., 2. и HTML-теги; не добавляет bullet-списки.
    """
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    for line in lines:
        if not line.strip():
            out.append("")
            continue
        stripped = _strip_leading_indent(line)
        if _NUMBERED_ITEM_RE.match(stripped):
            out.append(stripped)
            continue
        body = _BULLET_LEAD_RE.sub("", stripped).strip()
        while body.startswith("• "):
            body = body[2:].strip()
        if _is_list_section_header(body):
            out.append(body)
        else:
            out.append(body)
    return "\n".join(out)


def repair_telegram_html(text: str) -> str:
    """Закрывает незакрытые разрешённые теги (иначе Telegram: can't parse entities)."""
    if not text:
        return ""
    stack: list[str] = []
    parts: list[str] = []
    last = 0
    for match in _HTML_TAG_RE.finditer(text):
        parts.append(text[last : match.start()])
        closing = text[match.start() + 1] == "/"
        name = match.group(1).lower()
        if closing:
            while stack and stack[-1] != name:
                parts.append(f"</{stack.pop()}>")
            if stack and stack[-1] == name:
                stack.pop()
                parts.append(match.group(0))
        else:
            stack.append(name)
            parts.append(match.group(0))
        last = match.end()
    parts.append(text[last:])
    while stack:
        parts.append(f"</{stack.pop()}>")
    return "".join(parts)


def _escape_telegram_html(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )



def _split_markdown_table_segments(text: str) -> list[tuple[str, str]]:
    """Разбивает текст на куски ``('text'| 'table', content)``."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    segments: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        if _is_markdown_table_row(lines[i]):
            table_lines: list[str] = []
            while i < len(lines):
                line = lines[i]
                if _is_markdown_table_row(line):
                    table_lines.append(line)
                    i += 1
                    continue
                if not line.strip() and i + 1 < len(lines) and _is_markdown_table_row(lines[i + 1]):
                    i += 1
                    continue
                break
            segments.append(("table", "\n".join(table_lines)))
            continue
        text_lines: list[str] = []
        while i < len(lines) and not _is_markdown_table_row(lines[i]):
            text_lines.append(lines[i])
            i += 1
        chunk = "\n".join(text_lines).strip()
        if chunk:
            segments.append(("text", chunk))
    return segments


def _format_aligned_pre_table(rows: list[list[str]]) -> str:
    """Выровненная сетка в ``<pre>`` — Telegram не поддерживает ``<table>/<tr>/<td>``."""
    if not rows:
        return ""
    ncols = max(len(row) for row in rows)
    padded = [row + [""] * (ncols - len(row)) for row in rows]
    widths = [max(len(padded[r][c]) for r in range(len(padded))) for c in range(ncols)]

    def render_row(row: list[str]) -> str:
        cells = [row[c].ljust(widths[c]) for c in range(ncols)]
        return " │ ".join(cells)

    lines = [render_row(padded[0])]
    if len(padded) > 1:
        sep_len = sum(widths) + 3 * max(ncols - 1, 0)
        lines.append("─" * sep_len)
        for row in padded[1:]:
            lines.append(render_row(row))
    return f"<pre>{_escape_telegram_html(chr(10).join(lines))}</pre>"


def markdown_tables_to_telegram_html(text: str) -> str:
    """
    Markdown pipe-таблицы → читаемая HTML-вёрстка для Telegram.

    Клиенты Telegram не рендерят ``<table>``; используем моноширинный ``<pre>``
    с выравниванием колонок и разделителем под строкой заголовков.
    """
    if not (text or "").strip():
        return ""
    parts: list[str] = []
    for kind, chunk in _split_markdown_table_segments(text):
        if kind == "table":
            rows: list[list[str]] = []
            for line in chunk.split("\n"):
                if _is_markdown_table_separator(line):
                    continue
                if _is_markdown_table_row(line):
                    rows.append(_parse_markdown_table_row(line))
            table_html = _format_aligned_pre_table(rows)
            if table_html:
                parts.append(table_html)
            continue
        parts.append(markdown_to_html(chunk))
    return "\n\n".join(p for p in parts if p).strip()


def prepare_telegram_html_text(text: str, *, max_len: int = 4090) -> str:
    """HTML для Telegram: markdown→HTML, списки, починка тегов, обрезка длины."""
    raw = text or ""
    if "<pre>" in raw and "</pre>" in raw:
        cleaned = raw.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = repair_telegram_html(cleaned)
        if len(cleaned) > max_len:
            cleaned = cleaned[: max_len - 1] + "…"
        return cleaned
    cleaned = markdown_to_html(raw)
    cleaned = normalize_telegram_list_markup(cleaned)
    cleaned = repair_telegram_html(cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1] + "…"
    return cleaned


def sanitize_telegram_plain_text(text: str, *, max_len: int = 4090) -> str:
    """
    Убирает HTML-теги и обрезает длину для ``edit_message_text`` / ``answer`` без parse_mode.
    Markdown-символы оставляем как литералы (режим по умолчанию).
    """
    if not text:
        return ""
    cleaned = _TAG_RE.sub("", text)
    cleaned = _BROKEN_ENTITY_RE.sub("", cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1] + "…"
    return cleaned
