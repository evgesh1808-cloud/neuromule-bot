"""Тесты нормализации «плоского» ответа блогера без ===-маркеров."""

from __future__ import annotations

from services.blogger_post_parser import normalize_blogger_raw_output, parse_blogger_post

_FLAT_DOG_POST = """[Вариант 1 (Интрига)]: ⚡ Исчезновение на реке

[Вариант 2 (Боль аудитории)]: 💔 Кошмар туриста

[Вариант 3 (Хайп)]: 🔥 Утро без Стива

Адреналин бьёт ключом, когда любимый четвероногий друг растворяется в лесу.

[Вариант А (Вовлечение)]: 💬 А с вами случались истории?

[Вариант Б (Коммерческий)]: 🛡️ Защитите своего любимца

[Тематические]: #Собаки #Поход
[Навигационные]: #Блог_инсайт

A professional cinematic photo of lost dog in forest, 4k --ar 16:9
"""


def test_normalize_flat_blogger_post_adds_section_markers() -> None:
    normalized = normalize_blogger_raw_output(_FLAT_DOG_POST)
    assert normalized.startswith("===ХУКИ===")
    assert "===ТЕЛО ПОСТА===" in normalized
    assert "===ПРИЗЫВЫ К ДЕЙСТВИЮ===" in normalized
    assert "===ХЭШТЕГИ===" in normalized
    assert "===ПРОМПТ ДЛЯ КАРТИНКИ===" in normalized

    parsed = parse_blogger_post(normalized)
    assert parsed.body is not None
    assert "Адреналин" in parsed.body
    assert parsed.hashtags is not None
    assert "#Собаки" in parsed.hashtags
    assert parsed.image_prompt is not None
    assert "lost dog" in parsed.image_prompt
    assert "===ХЭШТЕГИ===" not in parsed.display_plain()
