"""Парсинг ответа режима «Блогер» с разделителями ``===``."""

from __future__ import annotations

from dataclasses import dataclass

_SECTION_ORDER: tuple[str, ...] = (
    "ХУКИ",
    "ТЕЛО ПОСТА",
    "ПРИЗЫВЫ К ДЕЙСТВИЮ",
    "ХЭШТЕГИ",
    "ПРОМПТ ДЛЯ КАРТИНКИ",
)

_DISPLAY_SECTIONS: tuple[str, ...] = ("ХУКИ", "ТЕЛО ПОСТА", "ПРИЗЫВЫ К ДЕЙСТВИЮ")


@dataclass(frozen=True)
class BloggerPostParsed:
    sections: dict[str, str]

    @property
    def hashtags(self) -> str | None:
        block = (self.sections.get("ХЭШТЕГИ") or "").strip()
        return block or None

    @property
    def image_prompt(self) -> str | None:
        block = (self.sections.get("ПРОМПТ ДЛЯ КАРТИНКИ") or "").strip()
        return block or None

    def display_plain(self) -> str:
        parts = [
            (self.sections.get(name) or "").strip()
            for name in _DISPLAY_SECTIONS
            if (self.sections.get(name) or "").strip()
        ]
        return "\n\n".join(parts)


def parse_blogger_post(raw_text: str) -> BloggerPostParsed:
    text = (raw_text or "").strip()
    sections: dict[str, str] = {}
    if not text:
        return BloggerPostParsed(sections=sections)

    for idx, name in enumerate(_SECTION_ORDER):
        marker = f"==={name}==="
        start = text.find(marker)
        if start == -1:
            continue
        content_start = start + len(marker)
        end = len(text)
        for next_name in _SECTION_ORDER[idx + 1 :]:
            next_marker = f"==={next_name}==="
            pos = text.find(next_marker, content_start)
            if pos != -1:
                end = min(end, pos)
        sections[name] = text[content_start:end].strip()
    return BloggerPostParsed(sections=sections)
