"""Парсинг ответа режима «Блогер» с разделителями ``===``."""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.blogger_image_prompt import sanitize_blogger_image_prompt_for_imagen

_SECTION_ORDER: tuple[str, ...] = (
    "ХУКИ",
    "ТЕЛО ПОСТА",
    "ПРИЗЫВЫ К ДЕЙСТВИЮ",
    "ХЭШТЕГИ",
    "ПРОМПТ ДЛЯ КАРТИНКИ",
)

_DISPLAY_SECTIONS: tuple[str, ...] = ("ХУКИ", "ТЕЛО ПОСТА", "ПРИЗЫВЫ К ДЕЙСТВИЮ")

_BLOGGER_DISPLAY_LABELS: dict[str, str] = {
    "ХУКИ": "💡 Варианты ярких заголовков:",
    "ТЕЛО ПОСТА": "✍️ Текст поста:",
    "ПРИЗЫВЫ К ДЕЙСТВИЮ": "📢 Варианты концовки (призыв к действию):",
}

_HEADER_ECHO_VALUES: frozenset[str] = frozenset(
    {
        "хуки",
        "хук",
        "тело поста",
        "тело",
        "призывы к действию",
        "призыв к действию",
        "cta",
        "хэштеги",
        "хештеги",
        "промпт для картинки",
        "промпт картинки",
    }
)

_CANONICAL_SECTIONS: dict[str, str] = {
    "ХУКИ": "ХУКИ",
    "ТЕЛО ПОСТА": "ТЕЛО ПОСТА",
    "ТЕЛО": "ТЕЛО ПОСТА",
    "ПРИЗЫВЫ К ДЕЙСТВИЮ": "ПРИЗЫВЫ К ДЕЙСТВИЮ",
    "ПРИЗЫВ К ДЕЙСТВИЮ": "ПРИЗЫВЫ К ДЕЙСТВИЮ",
    "CTA": "ПРИЗЫВЫ К ДЕЙСТВИЮ",
    "ХЭШТЕГИ": "ХЭШТЕГИ",
    "ХЕШТЕГИ": "ХЭШТЕГИ",
    "ПРОМПТ ДЛЯ КАРТИНКИ": "ПРОМПТ ДЛЯ КАРТИНКИ",
    "ПРОМПТ КАРТИНКИ": "ПРОМПТ ДЛЯ КАРТИНКИ",
    "IMAGE PROMPT": "ПРОМПТ ДЛЯ КАРТИНКИ",
}

_MARKER_FINDALL_RE = re.compile(
    r"===\s*("
    r"ХУКИ|ТЕЛО ПОСТА|ПРИЗЫВЫ К ДЕЙСТВИЮ|ХЭШТЕГИ|ПРОМПТ ДЛЯ КАРТИНКИ"
    r")\s*===",
    re.IGNORECASE,
)

_SECTION_PATTERN_RE = re.compile(
    r"===(ХУКИ|ТЕЛО ПОСТА|ПРИЗЫВЫ К ДЕЙСТВИЮ|ХЭШТЕГИ|ПРОМПТ ДЛЯ КАРТИНКИ)===\s*"
    r"(.*?)(?=\s*===(?:ХУКИ|ТЕЛО ПОСТА|ПРИЗЫВЫ К ДЕЙСТВИЮ|ХЭШТЕГИ|ПРОМПТ ДЛЯ КАРТИНКИ)===|\Z)",
    re.DOTALL | re.IGNORECASE,
)

_FENCED_WHOLE_RE = re.compile(
    r"^\s*```(?:html|markdown|md|text|json)?\s*\n?(.*?)\n?```\s*$",
    re.DOTALL | re.IGNORECASE,
)

_FENCE_OPEN_RE = re.compile(r"^\s*```(?:html|markdown|md|text|json)?\s*\n?", re.IGNORECASE)
_FENCE_CLOSE_RE = re.compile(r"\n?```\s*$")

_PREAMBLE_LINE_RE = re.compile(
    r"^(?:"
    r"вот (?:ваш |готовый )?пост|готово[!,.]?|ниже (?:ваш )?пост|"
    r"конечно[!,.]?|разумеется[!,.]?|отлично[!,.]?|"
    r"here(?:'s| is) (?:your )?post|sure[!,.]?|output:"
    r")",
    re.IGNORECASE,
)

_LOOSE_HEADER_RE = re.compile(
    r"(?im)^\s*(?:#{1,3}\s*)?(?:===\s*)?"
    r"(ХУКИ|ТЕЛО(?:\s+ПОСТА)?|ПРИЗЫВ(?:Ы)?\s+К\s+ДЕЙСТВИЮ|CTA|Х[ЕЭ]ШТЕГИ|"
    r"ПРОМПТ(?:\s+ДЛЯ)?\s+КАРТИНКИ|IMAGE\s+PROMPT)"
    r"(?:\s*===)?\s*:?\s*$"
)

_BOLD_MARKDOWN_RE = re.compile(r"\*\*(.+?)\*\*")
_BOLD_MARKDOWN_UNDER_RE = re.compile(r"__(.+?)__")
_MARKDOWN_HEADER_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_BOLD_HTML_OPEN_RE = re.compile(r"<b\b[^>]*>", re.IGNORECASE)

BloggerSections = dict[str, str]

MISSING_SECTION_PLACEHOLDER = "[Секция будет восстановлена при адаптации]"


@dataclass(frozen=True)
class BloggerPostParsed:
    sections: dict[str, str]

    @property
    def hashtags(self) -> str | None:
        block = (self.sections.get("ХЭШТЕГИ") or "").strip()
        if not block or block == MISSING_SECTION_PLACEHOLDER:
            return None
        return block

    @property
    def image_prompt(self) -> str | None:
        block = (self.sections.get("ПРОМПТ ДЛЯ КАРТИНКИ") or "").strip()
        if not block or block == MISSING_SECTION_PLACEHOLDER:
            return None
        return block

    @property
    def body(self) -> str | None:
        block = (self.sections.get("ТЕЛО ПОСТА") or "").strip()
        if not block or block == MISSING_SECTION_PLACEHOLDER:
            return None
        return block

    def display_plain(self) -> str:
        return format_blogger_display_plain(self.sections)


def _normalize_header_echo(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _is_header_echo(text: str) -> bool:
    return _normalize_header_echo(text) in _HEADER_ECHO_VALUES


def format_blogger_display_plain(sections: BloggerSections) -> str:
    """Текст для пользователя: секции с подписями, без служебных блоков."""
    blocks: list[str] = []
    for name in _DISPLAY_SECTIONS:
        block = (sections.get(name) or "").strip()
        if not block or block == MISSING_SECTION_PLACEHOLDER:
            continue
        label = _BLOGGER_DISPLAY_LABELS.get(name, name)
        blocks.append(f"{label}\n{block}")
    return "\n\n".join(blocks)


def format_blogger_display_html(sections: BloggerSections) -> str:
    """Telegram HTML: жирные подписи секций + содержимое."""
    blocks: list[str] = []
    for name in _DISPLAY_SECTIONS:
        block = (sections.get(name) or "").strip()
        if not block or block == MISSING_SECTION_PLACEHOLDER:
            continue
        label = _BLOGGER_DISPLAY_LABELS.get(name, name)
        blocks.append(f"<b>{label}</b>\n{block}")
    return "\n\n".join(blocks)


def is_blogger_response_degraded(sections: BloggerSections) -> bool:
    """Пустой/скелетный ответ модели (только названия секций без контента)."""
    hooks = (sections.get("ХУКИ") or "").strip()
    body = (sections.get("ТЕЛО ПОСТА") or "").strip()
    cta = (sections.get("ПРИЗЫВЫ К ДЕЙСТВИЮ") or "").strip()

    if hooks == MISSING_SECTION_PLACEHOLDER and body == MISSING_SECTION_PLACEHOLDER:
        return True

    if _is_header_echo(hooks) and (
        _is_header_echo(body) or body == MISSING_SECTION_PLACEHOLDER
    ):
        return True

    display = format_blogger_display_plain(sections)
    if len(display.strip()) < 80:
        return True

    if "[вариант" not in hooks.lower() and len(hooks) < 45 and len(body) < 80:
        return True

    if body != MISSING_SECTION_PLACEHOLDER and len(body) < 60 and cta == MISSING_SECTION_PLACEHOLDER:
        return True

    return False


def repair_blogger_telegram_html(text: str) -> str:
    """Автоисправление разметки тела поста для Telegram HTML."""
    result = (text or "").strip()
    if not result:
        return result

    result = _MARKDOWN_HEADER_RE.sub(r"<b>\1</b>", result)
    result = _BOLD_MARKDOWN_UNDER_RE.sub(r"<b>\1</b>", result)
    result = _BOLD_MARKDOWN_RE.sub(r"<b>\1</b>", result)

    open_count = len(_BOLD_HTML_OPEN_RE.findall(result))
    close_count = result.lower().count("</b>")
    if open_count > close_count:
        result += "</b>" * (open_count - close_count)
    return result


def _apply_section_placeholders(sections: dict[str, str]) -> BloggerSections:
    """Заполняет пустые секции плейсхолдером — без KeyError при адаптации."""
    filled = _ensure_all_sections(sections)
    for name in _SECTION_ORDER:
        if not filled[name]:
            filled[name] = MISSING_SECTION_PLACEHOLDER
    return filled


def _canonical_section_name(raw: str) -> str | None:
    key = re.sub(r"\s+", " ", (raw or "").strip().upper())
    key = key.replace("Э", "Е")  # ХЭШТЕГИ / ХЕШТЕГИ
    return _CANONICAL_SECTIONS.get(key)


def _unwrap_fenced_code_blocks(text: str) -> str:
    """Снимает обёртку ``` / ```html, если модель завернула весь ответ в code fence."""
    t = (text or "").strip()
    if not t:
        return t

    whole = _FENCED_WHOLE_RE.match(t)
    if whole:
        return whole.group(1).strip()

    if t.startswith("```"):
        t = _FENCE_OPEN_RE.sub("", t, count=1)
        t = _FENCE_CLOSE_RE.sub("", t)
    return t.strip()


def _strip_ai_preamble(text: str) -> str:
    """Убирает типичные преамбулы модели до первой секции."""
    t = (text or "").strip()
    if not t:
        return t

    first_marker = _MARKER_FINDALL_RE.search(t)
    loose = _LOOSE_HEADER_RE.search(t)
    cut_at = -1
    if first_marker:
        cut_at = first_marker.start()
    elif loose:
        cut_at = loose.start()
    if cut_at > 0:
        t = t[cut_at:]

    lines = t.splitlines()
    while lines:
        head = lines[0].strip()
        if not head:
            lines.pop(0)
            continue
        if _PREAMBLE_LINE_RE.match(head):
            lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()


def _ensure_all_sections(sections: dict[str, str]) -> BloggerSections:
    """Гарантирует все 5 ключей секций — без KeyError у потребителей."""
    return {name: (sections.get(name) or "").strip() for name in _SECTION_ORDER}


def _parse_sections_by_marker_findall(text: str) -> dict[str, str]:
    """Этап 1: ``re.findall`` по маркерам ``===СЕКЦИЯ===`` (lookahead до следующей секции)."""
    matches = _SECTION_PATTERN_RE.findall(text)
    if not matches:
        return {}

    sections: dict[str, str] = {}
    for tag, content in matches:
        name = _canonical_section_name(tag)
        chunk = (content or "").strip()
        if name and chunk:
            sections[name] = chunk
    return sections


def _parse_sections_by_loose_headers(text: str) -> dict[str, str]:
    """Этап 2: жёсткая нарезка по заголовкам ``ХУКИ`` / ``ТЕЛО`` без ``===``."""
    hits: list[tuple[int, int, str]] = []
    for match in _LOOSE_HEADER_RE.finditer(text):
        name = _canonical_section_name(match.group(1))
        if not name:
            continue
        hits.append((match.start(), match.end(), name))

    if not hits:
        return _parse_sections_by_find_labels(text)

    hits.sort(key=lambda item: item[0])
    sections: dict[str, str] = {}
    for idx, (_start, header_end, name) in enumerate(hits):
        content_end = hits[idx + 1][0] if idx + 1 < len(hits) else len(text)
        chunk = text[header_end:content_end].strip()
        if chunk and name not in sections:
            sections[name] = chunk
    return sections


def _parse_sections_by_find_labels(text: str) -> dict[str, str]:
    """Резерв: ``.find()`` по ключевым меткам в порядке следования секций."""
    label_variants: dict[str, tuple[str, ...]] = {
        "ХУКИ": ("===ХУКИ===", "ХУКИ", "Хуки"),
        "ТЕЛО ПОСТА": ("===ТЕЛО ПОСТА===", "ТЕЛО ПОСТА", "ТЕЛО", "Тело поста"),
        "ПРИЗЫВЫ К ДЕЙСТВИЮ": (
            "===ПРИЗЫВЫ К ДЕЙСТВИЮ===",
            "ПРИЗЫВЫ К ДЕЙСТВИЮ",
            "ПРИЗЫВ К ДЕЙСТВИЮ",
            "CTA",
        ),
        "ХЭШТЕГИ": ("===ХЭШТЕГИ===", "ХЭШТЕГИ", "ХЕШТЕГИ"),
        "ПРОМПТ ДЛЯ КАРТИНКИ": (
            "===ПРОМПТ ДЛЯ КАРТИНКИ===",
            "ПРОМПТ ДЛЯ КАРТИНКИ",
            "ПРОМПТ КАРТИНКИ",
        ),
    }

    positions: list[tuple[int, str, int]] = []
    for name in _SECTION_ORDER:
        for label in label_variants.get(name, (name,)):
            pos = text.find(label)
            if pos == -1:
                continue
            positions.append((pos, name, pos + len(label)))
            break

    if not positions:
        return {}

    positions.sort(key=lambda item: item[0])
    sections: dict[str, str] = {}
    for idx, (_pos, name, header_end) in enumerate(positions):
        content_end = positions[idx + 1][0] if idx + 1 < len(positions) else len(text)
        chunk = text[header_end:content_end].strip(" :\n\r\t-")
        if chunk:
            sections[name] = chunk
    return sections


def _parse_sections_flat_heuristic(text: str) -> dict[str, str]:
    """Этап 3: эвристика «плоского» ответа с [Вариант 1] / [Вариант А]."""
    variant1 = re.search(r"\[Вариант 1 \(", text)
    if not variant1:
        return {}

    t = text[variant1.start() :]
    variant3 = re.search(r"\[Вариант 3 \([^)]+\)\]:[^\n]*", t)
    if not variant3:
        return {"ХУКИ": t.strip()}

    hooks = t[: variant3.end()].strip()
    rest = t[variant3.end() :]

    variant_a = re.search(r"\[Вариант А \(", rest)
    thematic = re.search(r"\[Тематические\]", rest)
    image = re.search(r"A professional cinematic photo", rest, re.IGNORECASE)

    sections: dict[str, str] = {"ХУКИ": hooks}

    if variant_a:
        sections["ТЕЛО ПОСТА"] = rest[: variant_a.start()].strip()
        cta_end = thematic.start() if thematic else (image.start() if image else len(rest))
        sections["ПРИЗЫВЫ К ДЕЙСТВИЮ"] = rest[variant_a.start() : cta_end].strip()
    elif thematic or image:
        end = thematic.start() if thematic else (image.start() if image else len(rest))
        sections["ТЕЛО ПОСТА"] = rest[:end].strip()

    if thematic:
        hash_end = image.start() if image else len(rest)
        sections["ХЭШТЕГИ"] = rest[thematic.start() : hash_end].strip()
    if image:
        sections["ПРОМПТ ДЛЯ КАРТИНКИ"] = rest[image.start() :].strip()

    return {k: v for k, v in sections.items() if v}


def _extract_blogger_sections(text: str) -> dict[str, str]:
    """Двухэтапный (плюс flat) парсер секций блогера."""
    cleaned = _strip_ai_preamble(_unwrap_fenced_code_blocks(text))

    strict = _parse_sections_by_marker_findall(cleaned)
    if strict:
        return strict

    loose = _parse_sections_by_loose_headers(cleaned)
    if loose:
        return loose

    flat = _parse_sections_flat_heuristic(cleaned)
    if flat:
        return flat

    return {}


def parse_blogger_post(raw_text: str) -> BloggerPostParsed:
    text = (raw_text or "").strip()
    if not text:
        return BloggerPostParsed(sections={})
    sections = _extract_blogger_sections(text)
    if not sections:
        sections = _parse_sections_by_marker_findall(text)
    return BloggerPostParsed(sections=sections)


def _repair_sections(sections: dict[str, str]) -> dict[str, str]:
    """Применяет HTML-ремонт к телу и синхронную очистку промпта обложки."""
    repaired = dict(sections)
    body = (repaired.get("ТЕЛО ПОСТА") or "").strip()
    if body:
        repaired["ТЕЛО ПОСТА"] = repair_blogger_telegram_html(body)

    image_prompt = (repaired.get("ПРОМПТ ДЛЯ КАРТИНКИ") or "").strip()
    if image_prompt:
        repaired["ПРОМПТ ДЛЯ КАРТИНКИ"] = sanitize_blogger_image_prompt_for_imagen(image_prompt)
    return repaired


def reassemble_blogger_sections(sections: dict[str, str]) -> str:
    """Собирает канонический ``===``-текст из словаря секций (для кэша и отображения)."""
    blocks: list[str] = []
    for name in _SECTION_ORDER:
        block = (sections.get(name) or "").strip()
        if block and block != MISSING_SECTION_PLACEHOLDER:
            blocks.append(f"==={name}===\n{block}")
    return "\n\n".join(blocks)


def normalize_blogger_raw_output(raw_text: str) -> BloggerSections:
    """Ультимативный парсер режима «Блогер».

    Защищён от плоских ответов, грязного Markdown и падений Telegram API.

    1. Снять code fence (``` / ```html / ```json) и преамбулы ИИ.
    2. ``re.findall`` по ``===СЕКЦИЯ===``; fallback — regex/.find()/[Вариант 1].
    3. Санитизация HTML в ``ТЕЛО ПОСТА``; пустые секции — плейсхолдер.
    """
    t = (raw_text or "").strip()
    if not t:
        return _apply_section_placeholders({})

    cleaned = _strip_ai_preamble(_unwrap_fenced_code_blocks(t))
    sections = _extract_blogger_sections(cleaned)

    if not sections and cleaned:
        sections = {"ТЕЛО ПОСТА": cleaned}

    return _apply_section_placeholders(_repair_sections(sections))
