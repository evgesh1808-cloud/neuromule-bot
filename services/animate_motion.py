"""Участники и черновик сценария для пошагового оживления фото."""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.photo_edit_session import PhotoEditSession
from services.photo_multi_ref_routing import MAX_GROUP_REFS

MAX_ANIMATE_SURVEY_PEOPLE = MAX_GROUP_REFS

_PET_ROLE_MARKERS: tuple[str, ...] = (
    "кош",
    "собак",
    "cat",
    "dog",
    "pet",
    "питом",
    "щен",
    "кот",
    "котик",
    "пёс",
    "пес",
    "hamster",
    "rabbit",
)

_ROLE_DISPLAY_RU: dict[str, str] = {
    "папа": "Папа",
    "мама": "Мама",
    "дочка": "Дочка",
    "сын": "Сын",
    "дедушка": "Дедушка",
    "бабушка": "Бабушка",
    "тётя": "Тётя",
    "тетя": "Тётя",
    "дядя": "Дядя",
    "внук": "Внук",
    "внучка": "Внучка",
    "племянник": "Племянник",
    "племянница": "Племянница",
    "person": "Участник",
    "man/father": "Мужчина",
    "woman/mother": "Женщина",
    "first child": "Ребёнок 1",
    "second child": "Ребёнок 2",
}

MOTION_CHOICE_MAP: dict[str, tuple[str, str]] = {
    "sm": ("😊 Мягко улыбается", "very subtle closed-lip smile, barely perceptible, no teeth"),
    "lk": ("👀 Смотрит на соседа", "slowly turns head to look at the person below"),
    "wn": ("😉 Подмигивает", "playfully winks one eye"),
    "st": ("⏸️ Почти не двигается", "minimal natural eye blink and breathing only, neutral closed mouth"),
}

PET_MOTION_CHOICE_MAP: dict[str, tuple[str, str]] = {
    "pl": ("🐱 Играет/шевелит ушами", "subtly twitches ears and playfully moves"),
    "st": ("⏸️ Замер", "stays perfectly still"),
}

_MOTION_DEFAULT_STATIC = (
    "minimal natural eye blink and breathing only, neutral closed mouth, no smile, no head movement"
)


@dataclass(frozen=True, slots=True)
class AnimatePerson:
    """Человек в кадре (сверху вниз = Person 1, Person 2…)."""

    ref_index: int
    role_key: str
    display_label: str


@dataclass(frozen=True, slots=True)
class AnimatePet:
    ref_index: int
    role_key: str
    display_label: str


@dataclass(frozen=True, slots=True)
class AnimateParticipants:
    people: tuple[AnimatePerson, ...]
    pet: AnimatePet | None = None


def is_pet_role(role: str) -> bool:
    low = (role or "").strip().lower()
    if not low:
        return False
    return any(marker in low for marker in _PET_ROLE_MARKERS)


def display_role_label(role: str, *, ref_index: int) -> str:
    key = (role or "").strip().lower()
    if is_pet_role(key):
        if "кош" in key or "cat" in key or "кот" in key:
            return "Кошка"
        if "соб" in key or "dog" in key or "пёс" in key or "пес" in key or "щен" in key:
            return "Собака"
        return "Питомец"
    base = _ROLE_DISPLAY_RU.get(key)
    if base:
        return base
    if key.startswith("person") or key == "person":
        return f"Участник {ref_index + 1}"
    cleaned = (role or "").strip()
    if cleaned:
        return cleaned[0].upper() + cleaned[1:]
    return f"Участник {ref_index + 1}"


def format_participant_list_ru(participants: AnimateParticipants) -> str:
    """Список участников для intro-сообщения."""
    labels = [p.display_label for p in participants.people]
    if participants.pet is not None:
        labels.append(participants.pet.display_label)
    if not labels:
        return "1 участник"
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} и {labels[1]}"
    return ", ".join(labels[:-1]) + f" и {labels[-1]}"


def infer_ref_count_from_prompt(prompt: str) -> int:
    """
    Сколько персон в сцене:
    1) явные метки ``фото2 = дедушка`` / ``input_references[3] = тётя``;
    2) максимальный индекс ``input_references[N]`` в промпте;
    3) эвристика по упомянутым ролям (до MAX_GROUP_REFS).
    """
    from services.group_ref_slot_map import build_ordered_role_slots, parse_explicit_ref_slot_map

    text = (prompt or "").strip()
    if not text:
        return 0

    explicit = parse_explicit_ref_slot_map(text)
    if explicit:
        return min(MAX_GROUP_REFS, max(explicit.keys()) + 1)

    indices = [int(x) for x in re.findall(r"input_references\[(\d+)\]", text, flags=re.I)]
    if indices:
        return min(MAX_GROUP_REFS, max(indices) + 1)

    best = 0
    for count in range(MAX_GROUP_REFS, 1, -1):
        slots = build_ordered_role_slots(text, count)
        if len(slots) >= 2:
            span = max(slots.keys()) + 1 if slots else count
            best = max(best, span, len(slots))
    return min(MAX_GROUP_REFS, best)


def resolve_final_roles_from_session(session: PhotoEditSession | None) -> list[str]:
    """Роли из сессии генерации (peek-wall / group multi-ref)."""
    if session is None:
        return []
    return resolve_final_roles_from_context(
        final_roles=getattr(session, "final_roles", None) or (),
        group_ref_file_ids=session.group_ref_file_ids,
        group_base_prompt=session.group_base_prompt,
        user_prompt=session.user_prompt,
    )


def resolve_final_roles_from_context(
    *,
    final_roles: tuple[str, ...] | list[str] | None = None,
    group_ref_file_ids: tuple[str, ...] | list[str] | None = None,
    group_base_prompt: str | None = None,
    user_prompt: str | None = None,
) -> list[str]:
    stored = final_roles or ()
    if stored:
        from services.group_ref_slot_map import coerce_final_roles

        return coerce_final_roles(list(stored))

    prompt = (group_base_prompt or user_prompt or "").strip()
    refs = tuple((fid or "").strip() for fid in (group_ref_file_ids or ()) if (fid or "").strip())

    from services.group_ref_slot_map import (
        build_group_slot_layout,
        final_roles_from_layout,
        parse_explicit_ref_slot_map,
    )

    explicit = parse_explicit_ref_slot_map(prompt)
    count = len(refs) if len(refs) >= 2 else infer_ref_count_from_prompt(prompt)
    if explicit:
        count = max(count, max(explicit.keys()) + 1)
    if count < 2:
        return []

    count = min(MAX_GROUP_REFS, count)
    layout = build_group_slot_layout(prompt, [""] * count)
    roles = final_roles_from_layout(layout, count)

    if explicit:
        merged: list[str] = []
        for idx, role in enumerate(roles):
            if idx in explicit:
                merged.append(explicit[idx].strip()[:120])
            else:
                merged.append(role)
        return merged

    return roles


def resolve_animate_participants(session: PhotoEditSession | None) -> AnimateParticipants:
    """Разделяет final_roles на людей (сверху вниз) и питомца."""
    roles = resolve_final_roles_from_session(session)
    if not roles:
        return AnimateParticipants(
            people=(AnimatePerson(ref_index=0, role_key="person", display_label="Участник 1"),),
        )

    people: list[AnimatePerson] = []
    pet: AnimatePet | None = None
    for idx, role in enumerate(roles[:MAX_ANIMATE_SURVEY_PEOPLE]):
        label = display_role_label(role, ref_index=idx)
        if is_pet_role(role):
            pet = AnimatePet(ref_index=idx, role_key=role, display_label=label)
            continue
        people.append(AnimatePerson(ref_index=idx, role_key=role, display_label=label))

    if not people:
        people.append(AnimatePerson(ref_index=0, role_key="person", display_label="Участник 1"))

    return AnimateParticipants(people=tuple(people), pet=pet)


def build_motion_draft_from_choices(
    participants: AnimateParticipants,
    choices: dict[str, str],
) -> str:
    """Черновик кликов пользователя → текст для GPT-режиссёра."""
    lines: list[str] = []
    for person_idx, person in enumerate(participants.people, start=1):
        motion = (choices.get(f"p:{person.ref_index}") or "").strip()
        if not motion:
            motion = _MOTION_DEFAULT_STATIC
        lines.append(
            f"Person {person_idx} ({person.display_label}, input_references[{person.ref_index}]): {motion}"
        )
    if participants.pet is not None:
        pet_motion = (choices.get(f"pet:{participants.pet.ref_index}") or "").strip()
        if not pet_motion:
            pet_motion = "stays perfectly still"
        lines.append(
            f"Pet ({participants.pet.display_label}, input_references[{participants.pet.ref_index}]): {pet_motion}"
        )
    return "\n".join(lines)
