"""Explicit ref-index → role mapping and verbatim group prompt mode (Mish04-style)."""

from __future__ import annotations

import re

from services.multi_ref_scene_parser import SceneCharacter, SceneLayout

VERBATIM_GROUP_PROMPT_MIN_CHARS = 500

_IDENTITY_PRESERVE_MARKERS: tuple[str, ...] = (
    "не меняя",
    "не меняй",
    "не меня",
    "1:1",
    "1%",
    "100%",
    "identity preservation",
    "face identity",
    "сохран",
    "идентичност",
    "черт",
    "эталон",
    "input_references",
)

_EXPLICIT_PHOTO_ROLE_RE = re.compile(
    r"(?:"
    r"(?:фото|photo|ref|reference)\s*(\d+)\s*[=:\-→]\s*([^\n;,]+)"
    r"|input_references\[(\d+)\]\s*[=:\-→]\s*([^\n;,]+)"
    r")",
    re.IGNORECASE,
)

_ORDERED_ROLE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:мужчин\w*|пап\w*|отец|father|man|husband)\b", re.I), "man/father"),
    (re.compile(r"\b(?:женщин\w*|девушк\w*|мам\w*|mother|woman|wife)\b", re.I), "woman/mother"),
    (
        re.compile(r"\b(?:перв(?:ый|ого)?\s+реб[^\s]*|first\s+child)\b", re.I),
        "first child",
    ),
    (
        re.compile(r"\b(?:втор(?:ой|ого)?\s+реб[^\s]*|second\s+child)\b", re.I),
        "second child",
    ),
    (re.compile(r"\b(?:дочк\w*|daughter)\b", re.I), "daughter"),
    (re.compile(r"\b(?:сын\w*|son|мальчик)\b", re.I), "son"),
)

_CHILD_LABEL_MARKERS = frozenset(
    {
        "child",
        "daughter",
        "son",
        "first child",
        "second child",
        "дочь",
        "дочка",
        "сын",
        "ребенок",
        "ребёнок",
    }
)

_MOTHER_LABEL_MARKERS = frozenset(
    {
        "mother",
        "woman",
        "wife",
        "мама",
        "женщина",
        "девушка",
    }
)


def should_preserve_verbatim_group_prompt(user_prompt: str) -> bool:
    """Long or identity-heavy prompts: keep user text for scene (no LLM paraphrase)."""
    text = (user_prompt or "").strip()
    if len(text) >= VERBATIM_GROUP_PROMPT_MIN_CHARS:
        return True
    low = text.lower()
    return any(marker in low for marker in _IDENTITY_PRESERVE_MARKERS)


def parse_explicit_ref_slot_map(user_prompt: str) -> dict[int, str]:
    """Parse ``фото1 = папа`` / ``input_references[2] = дочка`` → {index: role label}."""
    text = (user_prompt or "").strip()
    if not text:
        return {}

    slots: dict[int, str] = {}
    for match in _EXPLICIT_PHOTO_ROLE_RE.finditer(text):
        photo_num = match.group(1)
        role_a = match.group(2)
        ref_idx_str = match.group(3)
        role_b = match.group(4)

        if ref_idx_str is not None:
            ref_idx = int(ref_idx_str)
            role = (role_b or "").strip()
        elif photo_num is not None:
            ref_idx = int(photo_num) - 1
            role = (role_a or "").strip()
        else:
            continue

        if ref_idx < 0 or ref_idx > 9 or not role:
            continue
        slots[ref_idx] = role[:120]
    return slots


def build_ordered_role_slots(user_prompt: str, num_refs: int) -> dict[int, str]:
    """Heuristic: first-mentioned roles in prompt → ref indices 0..N-1."""
    text = (user_prompt or "").strip()
    if not text or num_refs < 2:
        return {}

    found: list[tuple[int, str]] = []
    for pattern, label in _ORDERED_ROLE_RULES:
        for match in pattern.finditer(text):
            found.append((match.start(), label))

    if len(found) < 2:
        return {}

    found.sort(key=lambda item: item[0])
    seen_labels: set[str] = set()
    ordered_labels: list[str] = []
    for _, label in found:
        key = label.lower()
        if key in seen_labels:
            continue
        seen_labels.add(key)
        ordered_labels.append(label)

    slots: dict[int, str] = {}
    for idx, label in enumerate(ordered_labels[:num_refs]):
        slots[idx] = label
    return slots


def merge_ref_slot_maps(*maps: dict[int, str]) -> dict[int, str]:
    """Later maps override earlier; explicit user map should be passed last."""
    merged: dict[int, str] = {}
    for slot_map in maps:
        for ref_idx, role in slot_map.items():
            if 0 <= ref_idx <= 9 and (role or "").strip():
                merged[ref_idx] = role.strip()
    return merged


def _identity_disambiguation_clause(label: str) -> str:
    low = (label or "").strip().lower()
    if not low:
        return ""
    if any(marker in low for marker in _CHILD_LABEL_MARKERS):
        if any(m in low for m in ("daughter", "дочь", "дочка")):
            return (
                " ROLE: DAUGHTER (female child) — NOT the mother/woman; "
                "never blend with adult female reference."
            )
        if any(m in low for m in ("son", "сын", "мальчик")):
            return (
                " ROLE: SON (male child) — NOT the father/man; "
                "never blend with adult male reference."
            )
        if "first child" in low or "second child" in low:
            return (
                " ROLE: CHILD — distinct from ALL adults; "
                "never use mother or father reference for this face."
            )
        return " ROLE: CHILD — distinct from all adult references; never blend adult features."
    if any(marker in low for marker in _MOTHER_LABEL_MARKERS):
        return (
            " ROLE: MOTHER/WOMAN — NOT any child/daughter; "
            "never blend with child references."
        )
    if any(m in low for m in ("father", "man", "husband", "пап", "мужчин", "отец")):
        return " ROLE: FATHER/MAN — NOT any child; never blend with child references."
    return ""


def layout_from_ref_slots(
    slot_map: dict[int, str],
    face_descriptions: list[str],
    user_prompt: str,
) -> SceneLayout:
    """Build SceneLayout from explicit/heuristic slot map."""
    count = len(face_descriptions)
    characters: list[SceneCharacter] = []
    used: set[int] = set()

    for ref_idx in sorted(slot_map.keys()):
        if ref_idx < 0 or ref_idx >= count:
            continue
        label = slot_map[ref_idx]
        desc = (face_descriptions[ref_idx] or "").strip()
        characters.append(
            SceneCharacter(
                ref_index=ref_idx,
                label=label,
                placement="as described in the user scene prompt",
                appearance_anchor=desc[:200] if desc else f"match input_references[{ref_idx}]",
            )
        )
        used.add(ref_idx)

    for idx in range(count):
        if idx in used:
            continue
        desc = (face_descriptions[idx] or "").strip()
        characters.append(
            SceneCharacter(
                ref_index=idx,
                label=f"Person {idx + 1}",
                placement="as described in the user scene prompt",
                appearance_anchor=desc[:200] if desc else f"match input_references[{idx}]",
            )
        )

    scene = (user_prompt or "group portrait together").strip()
    return SceneLayout(characters=characters, scene_description_en=scene)


def identity_disambiguation_clause(label: str) -> str:
    """Public wrapper for identity line suffixes."""
    return _identity_disambiguation_clause(label)


def apply_explicit_ref_slots(
    layout: SceneLayout,
    explicit_slots: dict[int, str],
    face_descriptions: list[str],
) -> SceneLayout:
    """Override mapper labels with user-provided ``фотоN = role`` entries."""
    if not explicit_slots:
        return layout

    by_ref = {character.ref_index: character for character in layout.characters}
    for ref_idx, label in explicit_slots.items():
        if ref_idx < 0 or ref_idx >= len(face_descriptions):
            continue
        role = (label or "").strip()
        if not role:
            continue
        desc = (face_descriptions[ref_idx] or "").strip()
        prev = by_ref.get(ref_idx)
        by_ref[ref_idx] = SceneCharacter(
            ref_index=ref_idx,
            label=role,
            placement=(prev.placement if prev else "as in user scene prompt"),
            appearance_anchor=desc[:200] if desc else (prev.appearance_anchor if prev else f"match input_references[{ref_idx}]"),
        )

    for idx in range(len(face_descriptions)):
        if idx in by_ref:
            continue
        desc = (face_descriptions[idx] or "").strip()
        by_ref[idx] = SceneCharacter(
            ref_index=idx,
            label=f"Person {idx + 1}",
            placement="as in user scene prompt",
            appearance_anchor=desc[:200] if desc else f"match input_references[{idx}]",
        )

    characters = sorted(by_ref.values(), key=lambda c: c.ref_index)
    scene = (layout.scene_description_en or "").strip()
    return SceneLayout(characters=characters, scene_description_en=scene)
