"""Тесты валидации Premium Standard copy-pack."""

from __future__ import annotations

from services.copy_pack import (
    convert_md_fences_to_pre,
    is_premium_copy_pack_reply,
    normalize_copy_pack_reply,
)
from services.use_cases.chat_turn import clean_markdown_to_html


def test_is_premium_copy_pack_reply_accepts_valid() -> None:
    text = (
        "Готово! Разные стили на выбор (нажмите на текст, чтобы скопировать):\n\n"
        "🫀 <b>Эмоциональный и душевный</b>\n"
        "<pre>\nА\n</pre>\n\n"
        "💼 <b>Официальный и деловой</b>\n"
        "<pre>\nБ\n</pre>\n\n"
        "⚡ <b>Ультра-короткий экспресс</b>\n"
        "<pre>\nВ\n</pre>\n\n"
        "🎭 <b>Современный / С юмором</b>\n"
        "<pre>\nГ\n</pre>\n"
    )
    assert is_premium_copy_pack_reply(text) is True


def test_is_premium_copy_pack_reply_rejects_coach() -> None:
    text = (
        "Вы можете создать тёплое поздравление.\n"
        "1. Начните с обращения\n"
        "2. Добавьте пожелание\n"
        "📋 Пример поздравления\n"
        "С днём рождения!"
    )
    assert is_premium_copy_pack_reply(text) is False


def test_convert_md_fences_to_pre() -> None:
    raw = "intro\n```\nТекст один\n```\n```\nТекст два\n```"
    converted = convert_md_fences_to_pre(raw)
    assert converted.count("<pre>") == 2
    assert "```" not in converted
    assert "Текст один" in converted


def test_clean_markdown_preserves_pre_blocks() -> None:
    text = (
        "Готово! Разные стили на выбор (нажмите на текст, чтобы скопировать):\n"
        "<pre>\nНе трогай *это*\n</pre>\n"
        "<pre>\nВторой\n</pre>\n"
        "<pre>\nТретий\n</pre>"
    )
    out = clean_markdown_to_html(text)
    assert "<pre>" in out
    assert "Не трогай *это*" in out


def test_normalize_copy_pack_reply_strips() -> None:
    assert normalize_copy_pack_reply("  hi  ") == "hi"
