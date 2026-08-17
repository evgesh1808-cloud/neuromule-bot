"""Маршрутизация альбомов: composite (база + 2-е фото) vs group multi-ref (портреты)."""

from __future__ import annotations

_COMPOSITE_PRINT_KEYWORDS: tuple[str, ...] = (
    "принт",
    "print",
    "логотип",
    "logo",
    "футболк",
    "t-shirt",
    "tshirt",
    "худи",
    "hoodie",
    "свитшот",
    "sweatshirt",
    "куртк",
    "jacket",
    "одежд",
    "clothing",
    "надень",
    "надеть",
    "одень",
    "одеть",
    "добавь",
    "добавить",
    "add ",
    "вставь",
    "вставить",
    "помести",
    "поместить",
    "перенес",
    "перенест",
    "transfer",
    "наложи",
    "embed",
    "place on",
    "put on shirt",
    "graphic on",
    "фото на",
    "photo on",
    "на футбол",
    "на худи",
    "на одежд",
)

_COMPOSITE_MIRROR_KEYWORDS: tuple[str, ...] = (
    "зеркал",
    "mirror",
    "reflection",
    "reflect",
    "отражен",
    "отражени",
    "отражение",
)

# Взрослая + детская версия одного человека (принт / отражение) — composite, не group.
_COMPOSITE_SAME_PERSON_VARIANT_KEYWORDS: tuple[str, ...] = (
    "маленьк",
    "ребён",
    "ребен",
    "child",
    "younger",
    "young me",
    "детск",
    "в детстве",
    "younger self",
    "past self",
)

MIN_GROUP_REFS = 2
MAX_GROUP_REFS = 10


def is_mirror_reflection_intent(prompt: str) -> bool:
    low = (prompt or "").strip().lower()
    return any(keyword in low for keyword in _COMPOSITE_MIRROR_KEYWORDS)


def is_same_person_variant_intent(prompt: str) -> bool:
    low = (prompt or "").strip().lower()
    return any(keyword in low for keyword in _COMPOSITE_SAME_PERSON_VARIANT_KEYWORDS)


def is_composite_print_intent(prompt: str) -> bool:
    """True — 2 фото: принт на одежде, зеркало, перенос детского фото и т.п."""
    low = (prompt or "").strip().lower()
    if not low:
        return False
    if any(keyword in low for keyword in _COMPOSITE_PRINT_KEYWORDS):
        return True
    if is_mirror_reflection_intent(prompt):
        return True
    if is_same_person_variant_intent(prompt) and any(
        token in low
        for token in (
            "футбол",
            "принт",
            "print",
            "одеж",
            "худи",
            "зеркал",
            "отраж",
            "перенес",
            "помест",
            "встав",
        )
    ):
        return True
    return False


def should_route_album_as_composite(*, num_refs: int, prompt: str) -> bool:
    """Ровно 2 фото + composite-intent → dual composite refine."""
    return num_refs == 2 and bool((prompt or "").strip()) and is_composite_print_intent(prompt)


def should_route_album_as_group(*, num_refs: int, prompt: str) -> bool:
    """2–10 фото + промпт без composite-intent → group multi-ref (Nano Banana Pro)."""
    if not (prompt or "").strip():
        return False
    if num_refs < MIN_GROUP_REFS or num_refs > MAX_GROUP_REFS:
        return False
    return not is_composite_print_intent(prompt)
