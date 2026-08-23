"""Layout collage / photo booth multi-ref (2 refs → grid contact sheet)."""

from __future__ import annotations

import re

from services.openrouter_images import append_negative_prompt_directive

_COLLAGE_INTENT_MARKERS: tuple[str, ...] = (
    "фотобудк",
    "photo booth",
    "photobooth",
    "photostrip",
    "photo strip",
    "фотолент",
    "коллаж",
    "collage",
    "contact sheet",
    "сетк",
    "grid",
    "2×4",
    "2x4",
    "2 x 4",
    "2 колонк",
    "колонк",
    "× 4",
    "x 4",
    "4 ряд",
    "4 ряда",
    "кадр 1",
    "квадрат 1",
    "1 квадрат",
)

_VERTICAL_COLLAGE_MARKERS: tuple[str, ...] = (
    "9:16",
    "вертикаль",
    "vertical",
    "stories",
    "reels",
    "2 колонк",
    "две колонк",
)

_HORIZONTAL_COLLAGE_MARKERS: tuple[str, ...] = (
    "16:9",
    "горизонталь",
    "horizontal",
    "wide",
)

_COLLAGE_NEGATIVE_PROMPT = (
    "extra people, third person, stranger, bystander, invented face, duplicate stranger, "
    "blended faces, merged identities, face printed on clothing, photo on t-shirt, "
    "face on shirt, wrong grid layout, missing frames, 2x2 instead of requested grid"
)


def is_layout_collage_intent(prompt: str) -> bool:
    """True when user asks for photo booth / grid / collage layout."""
    low = (prompt or "").strip().lower()
    if not low:
        return False
    return any(marker in low for marker in _COLLAGE_INTENT_MARKERS)


def resolve_collage_aspect_ratio(prompt: str, current: str | None) -> str:
    """Pick aspect ratio for collage; default vertical strip 9:16."""
    from services.photo_aspect_ratio import (
        ASPECT_RATIO_STORIES,
        ASPECT_RATIO_WIDE,
        DEFAULT_PHOTO_ASPECT_RATIO,
        normalize_photo_aspect_ratio,
    )

    normalized = normalize_photo_aspect_ratio(current)
    if normalized != DEFAULT_PHOTO_ASPECT_RATIO:
        return normalized

    low = (prompt or "").strip().lower()
    if any(marker in low for marker in _HORIZONTAL_COLLAGE_MARKERS):
        return ASPECT_RATIO_WIDE
    if any(marker in low for marker in _VERTICAL_COLLAGE_MARKERS):
        return ASPECT_RATIO_STORIES
    if re.search(r"2\s*[×x]\s*4", low) or "4 ряд" in low:
        return ASPECT_RATIO_STORIES
    return ASPECT_RATIO_STORIES


def _collage_ref_binding_lines(num_refs: int, prompt: str) -> list[str]:
    from services.group_ref_slot_map import (
        build_ordered_role_slots,
        merge_ref_slot_maps,
        parse_explicit_ref_slot_map,
    )

    explicit = parse_explicit_ref_slot_map(prompt)
    ordered = build_ordered_role_slots(prompt, num_refs)
    slots = merge_ref_slot_maps(ordered, explicit)

    lines: list[str] = []
    for idx in range(num_refs):
        role = (slots.get(idx) or f"Person {idx + 1}").strip()
        lines.append(
            f"- {role} → use ONLY input_references[{idx}] for every frame where this person "
            f"appears; never substitute another reference or an invented face."
        )
    return lines


def build_collage_multi_ref_api_prompt(user_prompt: str, num_refs: int) -> str:
    """ChatCom-style light prompt: layout brief verbatim + minimal ref binding."""
    prompt = (user_prompt or "").strip()
    count = max(2, min(int(num_refs or 0), 10))
    ref_block = "\n".join(_collage_ref_binding_lines(count, prompt))

    header = (
        f"MULTI-REFERENCE PHOTO COLLAGE ({count} people).\n"
        f"There are exactly {count} people in input_references — use ONLY these faces.\n"
        "STRICTLY FORBIDDEN: third persons, strangers, extra heads/hands, invented partners, "
        "printing reference faces on clothing.\n"
        "Follow EVERY frame/cell in the grid exactly as described below.\n"
        "Clothing, poses, empty frames, and grid structure come from the user brief — "
        "NOT from reference outfits.\n"
        "Preserve facial identity from the assigned input_references; "
        "do not add under-eye bags, aging, or tired eyes.\n\n"
        "Reference binding:\n"
        f"{ref_block}\n\n"
        "USER COLLAGE BRIEF:\n"
    )
    return append_negative_prompt_directive(
        f"{header}{prompt}",
        negative=_COLLAGE_NEGATIVE_PROMPT,
    )
