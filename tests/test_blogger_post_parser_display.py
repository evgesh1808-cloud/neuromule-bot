"""Тесты парсинга display-HTML черновиков блогера."""

from __future__ import annotations

from services.blogger_post_parser import (
    canonicalize_blogger_cache_raw,
    extract_blogger_post_body,
    format_blogger_display_html,
    normalize_blogger_raw_output,
    parse_blogger_post,
    reassemble_blogger_sections,
)

_SAMPLE = """===ХУКИ===
[Вариант 1 (кликбейт)]: Заголовок
===ТЕЛО ПОСТА===
Текст с <b>инсайтом</b> для адаптации — длинный пост.
===ПРИЗЫВЫ К ДЕЙСТВИЮ===
[Вариант А]: Подпишись
===ХЭШТЕГИ===
#тест
===ПРОМПТ ДЛЯ КАРТИНКИ===
A professional cinematic photo"""


def test_parse_blogger_post_display_html_extracts_body() -> None:
    sections = normalize_blogger_raw_output(_SAMPLE)
    display = format_blogger_display_html(sections)
    parsed = parse_blogger_post(display)
    body = extract_blogger_post_body(display, parsed)
    assert body is not None
    assert "инсайтом" in body


def test_canonicalize_blogger_cache_raw_from_display() -> None:
    sections = normalize_blogger_raw_output(_SAMPLE)
    display = format_blogger_display_html(sections)
    canonical = canonicalize_blogger_cache_raw(display)
    assert "===ТЕЛО ПОСТА===" in canonical
    assert extract_blogger_post_body(canonical) is not None


def test_canonicalize_blogger_cache_raw_keeps_marker_format() -> None:
    canonical = reassemble_blogger_sections(normalize_blogger_raw_output(_SAMPLE))
    assert canonicalize_blogger_cache_raw(canonical) == canonical


def test_extract_blogger_image_prompt_from_canonical() -> None:
    from services.blogger_post_parser import extract_blogger_image_prompt

    canonical = reassemble_blogger_sections(normalize_blogger_raw_output(_SAMPLE))
    assert extract_blogger_image_prompt(canonical) is not None
    assert "cinematic" in extract_blogger_image_prompt(canonical).lower()
