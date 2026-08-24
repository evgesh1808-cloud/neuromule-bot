"""Участники и черновик сценария для пошагового оживления фото."""

from __future__ import annotations

from dataclasses import dataclass

from services.photo_edit_session import PhotoEditSession

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
    "person": "Участник",
}

MOTION_CHOICE_MAP: dict[str, tuple[str, str]] = {
    "sm": ("😊 Мягко улыбается", "softly smiles into the camera"),
    "lk": ("👀 Смотрит на соседа", "slowly turns head to look at the person below"),
    "wn": ("😉 Подмигивает", "playfully winks"),
    "st": ("⏸️ Не двигается", "stays static"),
}

PET_MOTION_CHOICE_MAP: dict[str, tuple[str, str]] = {
    "pl": ("🐱 Играет/шевелит ушами", "subtly twitches ears and playfully moves"),
    "st": ("⏸️ Замер", "stays perfectly still"),
}


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
    return role.strip().capitalize() or f"Участник {ref_index + 1}"


def resolve_final_roles_from_session(session: PhotoEditSession | None) -> list[str]:
    """Роли из сессии генерации (peek-wall / group multi-ref)."""
    if session is None:
        return []
    stored = getattr(session, "final_roles", None) or ()
    if stored:
        from services.group_ref_slot_map import coerce_final_roles

        return coerce_final_roles(list(stored))

    refs = session.group_ref_file_ids
    if len(refs) < 2:
        return []

    from services.group_ref_slot_map import build_group_slot_layout, final_roles_from_layout

    prompt = (session.group_base_prompt or session.user_prompt or "").strip()
    layout = build_group_slot_layout(prompt, [""] * len(refs))
    return final_roles_from_layout(layout, len(refs))


def resolve_animate_participants(session: PhotoEditSession | None) -> AnimateParticipants:
    """Разделяет final_roles на людей (сверху вниз) и питомца."""
    roles = resolve_final_roles_from_session(session)
    if not roles:
        return AnimateParticipants(
            people=(AnimatePerson(ref_index=0, role_key="person", display_label="Участник 1"),),
        )

    people: list[AnimatePerson] = []
    pet: AnimatePet | None = None
    for idx, role in enumerate(roles):
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
            motion = "stays static"
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
