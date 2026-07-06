"""Парсинг ответа режима «Блогер» с разделителями ``===``."""

from __future__ import annotations

import re
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

    @property
    def body(self) -> str | None:
        block = (self.sections.get("ТЕЛО ПОСТА") or "").strip()
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


def normalize_blogger_raw_output(text: str) -> str:
    """Восстанавливает ``===``-разметку, если модель выдала «плоский» конструктор."""
    t = (text or "").strip()
    if not t:
        return t

    hooks_idx = t.find("===ХУКИ===")
    if hooks_idx > 0:
        t = t[hooks_idx:]
    if "===ХУКИ===" in t:
        return t

    variant1 = re.search(r"\[Вариант 1 \(", t)
    if not variant1:
        return t
    t = t[variant1.start() :]

    variant3 = re.search(r"\[Вариант 3 \([^)]+\)\]:[^\n]*", t)
    if not variant3:
        return f"===ХУКИ===\n{t}"

    hooks = t[: variant3.end()].strip()
    rest = t[variant3.end() :]

    variant_a = re.search(r"\[Вариант А \(", rest)
    thematic = re.search(r"\[Тематические\]", rest)
    image = re.search(r"A professional cinematic photo", rest, re.IGNORECASE)

    body = ""
    cta = ""
    hashtags = ""
    image_prompt = ""

    if variant_a:
        body = rest[: variant_a.start()].strip()
        cta_end = thematic.start() if thematic else (image.start() if image else len(rest))
        cta = rest[variant_a.start() : cta_end].strip()
    else:
        body = rest[: thematic.start() if thematic else (image.start() if image else len(rest))].strip()

    if thematic:
        hash_end = image.start() if image else len(rest)
        hashtags = rest[thematic.start() : hash_end].strip()
    if image:
        image_prompt = rest[image.start() :].strip()

    blocks = [f"===ХУКИ===\n{hooks}"]
    if body:
        blocks.append(f"===ТЕЛО ПОСТА===\n{body}")
    if cta:
        blocks.append(f"===ПРИЗЫВЫ К ДЕЙСТВИЮ===\n{cta}")
    if hashtags:
        blocks.append(f"===ХЭШТЕГИ===\n{hashtags}")
    if image_prompt:
        blocks.append(f"===ПРОМПТ ДЛЯ КАРТИНКИ===\n{image_prompt}")
    return "\n\n".join(blocks)
