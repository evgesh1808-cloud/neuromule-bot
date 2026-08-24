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

_CHILD_FACE_MARKERS = frozenset(
    {
        "girl child",
        "boy child",
        "female child",
        "male child",
        "child",
        "kid",
        "teen",
        "teenage",
        "adolescent",
        "preteen",
        "minor",
        "schoolgirl",
        "schoolboy",
        "школьниц",
        "подрост",
    }
)
_ADULT_FEMALE_FACE_MARKERS = frozenset(
    {
        "adult female",
        "woman",
        "mother",
        "lady",
        "female",
        "мам",
        "женщ",
    }
)
_ADULT_MALE_FACE_MARKERS = frozenset(
    {
        "adult male",
        "man",
        "father",
        "male",
        "beard",
        "dad",
        "мужчин",
        "пап",
        "отец",
    }
)

_ADULT_ROLE_LABELS = frozenset(
    {
        "man/father",
        "woman/mother",
        "mother",
        "father",
        "mother/woman",
        "man",
        "woman",
        "wife",
        "husband",
        "мама",
        "папа",
        "мужчин",
        "женщин",
        "девушка",
    }
)
_CHILD_ROLE_LABELS = frozenset(
    {
        "daughter",
        "son",
        "first child",
        "second child",
        "child",
        "дочь",
        "дочка",
        "сын",
        "ребенок",
        "ребёнок",
    }
)

CHILD_APPEARANCE_ANCHOR_MAX_CHARS = 400

_CHILD_LABEL_MARKERS = _CHILD_ROLE_LABELS

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


def _is_child_face_description(desc: str) -> bool:
    low = (desc or "").strip().lower()
    if not low:
        return False
    if "girl child" in low or "boy child" in low:
        return True
    if "adult female" in low or "adult male" in low:
        return False
    if any(marker in low for marker in _CHILD_FACE_MARKERS):
        if "adult" in low:
            return False
        return True
    return False


def _is_adult_female_face_description(desc: str) -> bool:
    low = (desc or "").strip().lower()
    if not low or _is_child_face_description(desc):
        return False
    if "adult female" in low:
        return True
    if "girl child" in low or "boy child" in low:
        return False
    return any(marker in low for marker in _ADULT_FEMALE_FACE_MARKERS)


def _is_adult_male_face_description(desc: str) -> bool:
    low = (desc or "").strip().lower()
    if not low or _is_child_face_description(desc):
        return False
    if "adult male" in low:
        return True
    if "girl child" in low or "boy child" in low:
        return False
    return any(marker in low for marker in _ADULT_MALE_FACE_MARKERS)


def _is_adult_role_label(label: str) -> bool:
    low = (label or "").strip().lower()
    return any(marker in low for marker in _ADULT_ROLE_LABELS) and not any(
        marker in low for marker in _CHILD_ROLE_LABELS
    )


def _is_child_role_label(label: str) -> bool:
    low = (label or "").strip().lower()
    return any(marker in low for marker in _CHILD_ROLE_LABELS)


def _appearance_anchor_for_ref(
    ref_idx: int,
    face_descriptions: list[str],
    role_label: str,
) -> str:
    desc = (face_descriptions[ref_idx] if ref_idx < len(face_descriptions) else "").strip()
    if not desc:
        return f"match input_references[{ref_idx}]"
    limit = (
        CHILD_APPEARANCE_ANCHOR_MAX_CHARS
        if _is_child_role_label(role_label) or _is_child_face_description(desc)
        else 200
    )
    return desc[:limit]


def infer_slots_from_face_descriptions(face_descriptions: list[str]) -> dict[int, str]:
    """Map ref indices from face-describe gender/age cues (upload order may be wrong)."""
    slots: dict[int, str] = {}
    for idx, desc in enumerate(face_descriptions):
        low = (desc or "").strip().lower()
        if not low:
            continue
        if "girl child" in low or ("girl" in low and "child" in low):
            slots[idx] = "daughter"
        elif "boy child" in low or ("boy" in low and "child" in low):
            slots[idx] = "son"
        elif "daughter" in low or "female child" in low:
            slots[idx] = "daughter"
        elif "son" in low or "male child" in low:
            slots[idx] = "son"
        elif _is_child_face_description(desc):
            if any(w in low for w in ("girl", "female", "daughter")):
                slots[idx] = "daughter"
            elif any(w in low for w in ("boy", "male", "son")):
                slots[idx] = "son"
        elif _is_adult_female_face_description(desc):
            slots[idx] = "mother/woman"
        elif _is_adult_male_face_description(desc):
            slots[idx] = "man/father"
    return slots


def sanitize_mapper_slots_with_face(
    mapper_slots: dict[int, str],
    face_descriptions: list[str],
) -> dict[int, str]:
    """Never let LLM mapper assign adult role to child face ref (or vice versa)."""
    face_slots = infer_slots_from_face_descriptions(face_descriptions)
    if not face_slots:
        return dict(mapper_slots)

    sanitized: dict[int, str] = {}
    for ref_idx, label in mapper_slots.items():
        if ref_idx < 0 or ref_idx >= len(face_descriptions):
            continue
        face_role = face_slots.get(ref_idx)
        mapped = (label or "").strip()
        if not mapped:
            continue
        if face_role in ("daughter", "son") and _is_adult_role_label(mapped):
            sanitized[ref_idx] = face_role
            continue
        if face_role in ("mother/woman", "man/father") and _is_child_role_label(mapped):
            sanitized[ref_idx] = face_role
            continue
        sanitized[ref_idx] = mapped

    for ref_idx, face_role in face_slots.items():
        if ref_idx not in sanitized:
            sanitized[ref_idx] = face_role
    return sanitized


def resolve_child_labels_from_faces(
    slot_map: dict[int, str],
    face_descriptions: list[str],
) -> dict[int, str]:
    """Turn generic first/second child into daughter/son using face-desc gender."""
    updated = dict(slot_map)
    for ref_idx, label in list(updated.items()):
        low = (label or "").lower()
        if "first child" not in low and "second child" not in low:
            continue
        face_role = infer_slots_from_face_descriptions(face_descriptions).get(ref_idx)
        if face_role in ("daughter", "son"):
            updated[ref_idx] = face_role
    return updated


def reconcile_layout_with_face_slots(
    layout: SceneLayout,
    face_descriptions: list[str],
) -> SceneLayout:
    """Replace generic first/second child labels with daughter/son from face describe."""
    face_slots = infer_slots_from_face_descriptions(face_descriptions)
    if not face_slots:
        return layout

    updated: list[SceneCharacter] = []
    for character in layout.characters:
        idx = character.ref_index
        face_role = face_slots.get(idx)
        if not face_role:
            updated.append(character)
            continue
        low_label = character.label.lower()
        generic_child = "child" in low_label or "person" in low_label
        generic_adult = "person" in low_label
        if face_role in ("daughter", "son") and generic_child:
            desc = (face_descriptions[idx] or "").strip()
            updated.append(
                character.model_copy(
                    update={
                        "label": face_role,
                        "appearance_anchor": desc[:200] if desc else character.appearance_anchor,
                    }
                )
            )
        elif face_role in ("mother/woman", "man/father") and generic_adult:
            desc = (face_descriptions[idx] or "").strip()
            updated.append(
                character.model_copy(
                    update={
                        "label": face_role,
                        "appearance_anchor": desc[:200] if desc else character.appearance_anchor,
                    }
                )
            )
        elif face_role in ("daughter", "son", "mother/woman", "man/father"):
            desc = (face_descriptions[idx] or "").strip()
            updated.append(
                character.model_copy(
                    update={
                        "label": face_role,
                        "appearance_anchor": desc[:200] if desc else character.appearance_anchor,
                    }
                )
            )
        else:
            updated.append(character)
    return SceneLayout(characters=updated, scene_description_en=layout.scene_description_en)


_VERTICAL_PEEK_MARKERS: tuple[str, ...] = (
    "подглядыва",
    "выглядыва",
    "peek",
    "peeking",
    "из-за",
    "за стен",
    "за вертикаль",
    "матовой",
)

_VERTICAL_STACK_PLACEMENTS: tuple[str, ...] = (
    "top of vertical peek stack (position 1 from top, below wall edge)",
    "second from top in vertical peek stack (position 2)",
    "third from top in vertical peek stack (position 3)",
    "fourth from top / bottom of vertical peek stack (position 4)",
)


def _role_vertical_rank(label: str) -> int:
    low = (label or "").lower()
    if any(k in low for k in ("father", "man", "husband", "мужчин", "пап", "отец")):
        return 0
    if any(k in low for k in ("mother", "woman", "wife", "женщин", "мам", "девушк")):
        return 1
    if any(k in low for k in ("first child", "перв")):
        return 2
    if any(k in low for k in ("daughter", "дочь", "дочка")) and "mother" not in low:
        return 2
    if any(k in low for k in ("second child", "втор")):
        return 3
    if any(k in low for k in ("son", "сын", "мальчик")):
        return 3
    if any(k in low for k in ("child", "реб")):
        return 2
    return 50


def apply_vertical_peek_placements(layout: SceneLayout, user_prompt: str) -> SceneLayout:
    """Assign vertical stack positions for wall-peek family compositions."""
    low = (user_prompt or "").lower()
    if not any(marker in low for marker in _VERTICAL_PEEK_MARKERS):
        return layout

    by_ref = {character.ref_index: character for character in layout.characters}
    ranked = sorted(layout.characters, key=lambda c: (_role_vertical_rank(c.label), c.ref_index))
    for pos, character in enumerate(ranked[: len(_VERTICAL_STACK_PLACEMENTS)]):
        placement = _VERTICAL_STACK_PLACEMENTS[pos]
        by_ref[character.ref_index] = character.model_copy(update={"placement": placement})

    characters = sorted(by_ref.values(), key=lambda c: c.ref_index)
    return SceneLayout(characters=characters, scene_description_en=layout.scene_description_en)


_PEEK_WALL_ROLE_ALIASES: dict[str, str] = {
    "папа": "папа",
    "отец": "папа",
    "father": "папа",
    "man/father": "папа",
    "man": "папа",
    "husband": "папа",
    "мужчина": "папа",
    "мама": "мама",
    "mother": "мама",
    "woman/mother": "мама",
    "woman": "мама",
    "wife": "мама",
    "женщина": "мама",
    "девушка": "мама",
    "дочка": "дочка",
    "дочь": "дочка",
    "daughter": "дочка",
    "девочка": "дочка",
    "girl": "дочка",
    "сын": "сын",
    "son": "сын",
    "мальчик": "сын",
    "boy": "сын",
}


def is_peek_wall_group_scene(user_prompt: str) -> bool:
    """True when user intent matches vertical wall-peek family composition."""
    low = (user_prompt or "").strip().lower()
    if not low:
        return False
    if any(marker in low for marker in _VERTICAL_PEEK_MARKERS):
        return True
    return any(token in low for token in ("прятки", "пряток", "peek stack", "за стен"))


def normalize_peek_wall_role(role: str) -> str:
    """Normalize free-text / mapper label → canonical peek-wall role."""
    low = (role or "").strip().lower()
    if not low:
        return "person"
    if low in _PEEK_WALL_ROLE_ALIASES:
        return _PEEK_WALL_ROLE_ALIASES[low]
    if any(k in low for k in ("пап", "father", "man/father", "husband", "отец", "мужчин")):
        if not any(k in low for k in ("мам", "woman")):
            return "папа"
    if any(k in low for k in ("мам", "mother", "woman/mother", "wife", "женщин", "девушк")):
        if not any(k in low for k in ("доч", "daughter")):
            return "мама"
    if any(k in low for k in ("доч", "daughter", "девоч")):
        return "дочка"
    if any(k in low for k in ("сын", "son", "мальчик")):
        return "сын"
    return low


def coerce_final_roles(
    final_roles: dict[int, str] | list[str] | tuple[str, ...],
) -> list[str]:
    """``{ref_index: role}`` or ordered list → roles aligned with input_references."""
    if isinstance(final_roles, dict):
        if not final_roles:
            return []
        indices = sorted(idx for idx in final_roles if idx >= 0)
        if not indices:
            return []
        return [
            normalize_peek_wall_role(final_roles[i]) if i in final_roles else "person"
            for i in range(indices[-1] + 1)
        ]
    return [normalize_peek_wall_role(role) for role in final_roles]


def final_roles_from_layout(layout: SceneLayout, num_refs: int) -> list[str]:
    """Build ordered role list from SceneLayout characters (index = input_references)."""
    by_ref = {int(c.ref_index): c.label for c in layout.characters}
    return [
        normalize_peek_wall_role(by_ref.get(i, f"Person {i + 1}"))
        for i in range(max(0, num_refs))
    ]


def _peek_wall_role_marker(idx: int, role: str, *, has_mama: bool) -> str:
    if role == "папа":
        return f"Мужчина (папа, input_references[{idx}])"
    if role == "мама":
        return f"Женщина (мама, input_references[{idx}])"
    if role == "дочка":
        suffix = ", NOT the woman/mother" if has_mama else ""
        return (
            f"Дочка (девочка, input_references[{idx}]{suffix}) — "
            f"100% точное копирование лица дочери строго с input_references[{idx}]: "
            "идентичные глаза, нос, губы, овал лица, пропорции ребёнка, цвет и длина волос"
        )
    if role == "сын":
        return f"Сын (мальчик, input_references[{idx}])"
    return f"Персонаж ({role}, input_references[{idx}])"


def _peek_wall_pose_line(idx: int, total: int) -> str:
    if idx == 0:
        return " — наклоняет торс из-за стены на самом верху."
    if idx == total - 1:
        return " — аккуратно выглядывает на самом нижнем уровне под предыдущим, слегка наклоняясь вперед."
    return " — высовывает голову еще ниже под предыдущим, наклоняясь вперед."


def _peek_wall_clothing(role: str) -> str:
    if role == "мама":
        return " ОДЕТА В ЧЕРНУЮ МАЙКУ НА ТОНКИХ БРЕТЕЛЬКАХ."
    return " Одет(а) в черную футболку."


def _peek_wall_emotions(role: str) -> str:
    if role in ("дочка", "сын"):
        eyes = "сфокусированные" if role == "сын" else "блестящие"
        return (
            " Рот закрыт, милая улыбка без зубов. "
            f"Глаза невероятно живые, {eyes}, осознанный взгляд строго в камеру "
            "(engaged eye-contact), четкие блики (crisp catchlights)."
        )
    if role == "папа":
        return (
            " Рот закрыт, на губах легкая полуулыбка. "
            "Глаза невероятно живые, осознанный взгляд прямо в камеру "
            "(engaged eye-contact), в зрачках четкие студийные блики (crisp catchlights)."
        )
    if role == "мама":
        return (
            " Рот плотно закрыт, спокойная легкая улыбка. "
            "Глаза невероятно живые, влажные, осознанный взгляд прямо в камеру "
            "(engaged eye-contact), яркие студийные блики (crisp catchlights) в зрачках."
        )
    return (
        " Рот закрыт, спокойная легкая улыбка. "
        "Глаза невероятно живые, осознанный взгляд прямо в камеру "
        "(engaged eye-contact), четкие блики (crisp catchlights)."
    )


def build_structured_multi_ref_prompt(
    final_roles: dict[int, str] | list[str] | tuple[str, ...],
) -> str:
    """
    Динамический шаблон «прятки за стеной» для multi-ref OpenRouter.

    ``final_roles``: индекс = позиция в ``input_references``, значение = роль
    (``папа``, ``мама``, ``дочка``, ``сын`` и синонимы).
    """
    roles = coerce_final_roles(final_roles)
    count = len(roles)
    if count < 2:
        raise ValueError("build_structured_multi_ref_prompt requires at least 2 roles")

    has_mama = "мама" in roles
    header = (
        f"Динамичное групповое фото {count} человек с закрытыми ртами. "
        "100% точное копирование лиц строго по эталонным фото input_references.\n"
        f"Композиция «прятки»: слева стоит массивная вертикальная белая стена. "
        f"Все {count} героя полностью спрятаны ЗА стеной, но их головы и плечи игриво "
        "ИЗ-ЗА неё ВЫГЛЯДЫВАЮТ на разной высоте, образуя чистую, плавно выровненную "
        "вертикальную линию.\n"
        "Физика поз, ОДЕЖДА и ЭМОЦИИ строго сверху вниз:"
    )

    person_lines: list[str] = []
    for idx, role in enumerate(roles):
        line = (
            f"{idx + 1}. {_peek_wall_role_marker(idx, role, has_mama=has_mama)}"
            f"{_peek_wall_pose_line(idx, count)}"
            f"{_peek_wall_clothing(role)}"
            f"{_peek_wall_emotions(role)}"
        )
        person_lines.append(line)

    footer = (
        "Все герои физически держатся руками за край стены, пальцы детализированы.\n"
        f"У ВСЕХ {count} ГЕРОЕВ БЕЗ ИСКЛЮЧЕНИЯ рты закрыты (mouths closed), "
        "полностью отсутствуют удивленные или открытые рты, нет пустых или стеклянных глаз.\n"
        "Студийный свет, теплый нежный бэкграунд-градиент справа, мягкие тени. "
        "Детализированная кожа, волосы, бархатистая текстура стены. Размытый фон. "
        "Все персонажи одеты исключительно в одинаковую черную одежду. "
        "Вертикальный студийный портрет 4K, формат 9:16"
    )

    return "\n".join([header, *person_lines, footer])


def build_group_slot_layout(
    user_prompt: str,
    face_descriptions: list[str],
    mapped_layout: SceneLayout | None = None,
) -> SceneLayout:
    """Merge face-inferred slots (primary), mapper, and ordered text; apply vertical peek."""
    prompt = (user_prompt or "").strip()
    count = len(face_descriptions)
    face_slots = infer_slots_from_face_descriptions(face_descriptions)
    ordered = build_ordered_role_slots(prompt, count)

    slot_map: dict[int, str] = {}
    if mapped_layout is not None:
        mapper_slots = sanitize_mapper_slots_with_face(
            {c.ref_index: c.label for c in mapped_layout.characters},
            face_descriptions,
        )
        slot_map = merge_ref_slot_maps(slot_map, mapper_slots)

    slot_map = merge_ref_slot_maps(slot_map, ordered)
    # Face-desc age/gender always wins over LLM mapper and text-order heuristics.
    slot_map = merge_ref_slot_maps(slot_map, face_slots)
    slot_map = resolve_child_labels_from_faces(slot_map, face_descriptions)

    layout = layout_from_ref_slots(slot_map, face_descriptions, prompt)
    layout = reconcile_layout_with_face_slots(layout, face_descriptions)
    return apply_vertical_peek_placements(layout, prompt)


def _identity_disambiguation_clause(label: str) -> str:
    low = (label or "").strip().lower()
    if not low:
        return ""
    if any(marker in low for marker in _CHILD_LABEL_MARKERS):
        if any(m in low for m in ("daughter", "дочь", "дочка")):
            return (
                " ROLE: DAUGHTER (female child) — NOT the mother/woman; "
                "never blend with adult female reference; reproduce exact eye shape, "
                "nose, lips, jawline, child proportions, and HAIR COLOR/STYLE/LENGTH "
                "from this reference only — hair must match reference within 0% deviation."
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
                appearance_anchor=_appearance_anchor_for_ref(ref_idx, face_descriptions, label),
            )
        )
        used.add(ref_idx)

    for idx in range(count):
        if idx in used:
            continue
        desc = (face_descriptions[idx] or "").strip()
        label = f"Person {idx + 1}"
        characters.append(
            SceneCharacter(
                ref_index=idx,
                label=label,
                placement="as described in the user scene prompt",
                appearance_anchor=_appearance_anchor_for_ref(idx, face_descriptions, label),
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
            appearance_anchor=_appearance_anchor_for_ref(ref_idx, face_descriptions, role),
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
