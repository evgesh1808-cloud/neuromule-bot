"""Парсинг Markdown pipe-таблиц (роль table_generator)."""

from __future__ import annotations

import re

_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")


def is_markdown_table_row(line: str) -> bool:
    return bool(_TABLE_ROW_RE.match(line or ""))


def is_markdown_table_separator(line: str) -> bool:
    if not is_markdown_table_row(line):
        return False
    inner = line.strip().strip("|").replace("|", "").strip()
    return bool(inner) and all(ch in "-: " for ch in inner)


def parse_markdown_table_row(line: str) -> list[str]:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return [re.sub(r"\*\*(.*?)\*\*", r"\1", cell) for cell in cells]


def extract_primary_markdown_table(text: str) -> list[list[str]] | None:
    """Первая таблица из ответа модели или ``None``."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    rows: list[list[str]] = []
    in_table = False
    for line in lines:
        if is_markdown_table_row(line):
            if is_markdown_table_separator(line):
                in_table = True
                continue
            rows.append(parse_markdown_table_row(line))
            in_table = True
            continue
        if in_table and rows:
            break
    return rows if rows else None


def normalize_table_rows(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return []
    ncols = max(len(row) for row in rows)
    return [row + [""] * (ncols - len(row)) for row in rows]


def rows_to_markdown_table(rows: list[list[str]]) -> str:
    """Таблица строк → Markdown pipe для промпта OpenRouter."""
    rows = normalize_table_rows(rows)
    if not rows:
        return ""
    ncol = len(rows[0])
    sep = "| " + " | ".join("---" for _ in range(ncol)) + " |"
    lines = ["| " + " | ".join(rows[0]) + " |", sep]
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)

