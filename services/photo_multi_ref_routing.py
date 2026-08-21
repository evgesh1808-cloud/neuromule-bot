"""Маршрутизация альбомов: 2+ фото → group multi-ref; composite только для мерча."""

from __future__ import annotations

MIN_GROUP_REFS = 2
MAX_GROUP_REFS = 10

# Composite (лицо + принт на одежде) — только при явном merch-intent в промпте.
_COMPOSITE_MERCH_KEYWORDS: tuple[str, ...] = (
    "принт",
    "футболк",
    "на одежду",
    "мерч",
)


def is_composite_merch_intent(prompt: str) -> bool:
    """True — пользователь просит принт/мерч на одежде (не групповой портрет)."""
    low = (prompt or "").strip().lower()
    if not low:
        return False
    return any(keyword in low for keyword in _COMPOSITE_MERCH_KEYWORDS)


# Backward-compatible alias для composite/OpenRouter call-sites.
is_composite_print_intent = is_composite_merch_intent


def should_route_album_as_composite(*, num_refs: int, prompt: str) -> bool:
    """Composite: ровно 2 фото + merch-ключи в промпте."""
    if num_refs != 2:
        return False
    return is_composite_merch_intent(prompt)


def should_route_as_group_multi_ref(*, num_refs: int, prompt: str) -> bool:
    """2+ фото и не composite → group multi-ref (ChatGPT-style)."""
    if num_refs < MIN_GROUP_REFS:
        return False
    return not should_route_album_as_composite(num_refs=num_refs, prompt=prompt)
