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
    "face on shirt, wrong grid layout, missing frames, 2x2 instead of requested grid, "
    "empty frame, blank frame, white void, repeated empty panels"
)

COLLAGE_SHEET_MAX_FACE_SIDE_PX = 720
COLLAGE_SHEET_GAP_PX = 12

_FEMALE_ROLE_MARKERS = frozenset(
    {"woman", "mother", "wife", "girl", "daughter", "female", "девуш", "женщ", "мам", "доч"}
)
_MALE_ROLE_MARKERS = frozenset(
    {"man", "father", "husband", "boy", "son", "male", "парень", "мужчин", "пап", "сын"}
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


def role_label_for_ref(prompt: str, ref_idx: int, num_refs: int) -> str:
    from services.group_ref_slot_map import (
        build_ordered_role_slots,
        merge_ref_slot_maps,
        parse_explicit_ref_slot_map,
    )

    slots = merge_ref_slot_maps(
        build_ordered_role_slots(prompt, num_refs),
        parse_explicit_ref_slot_map(prompt),
    )
    return (slots.get(ref_idx) or f"Person {ref_idx + 1}").strip()


def _is_female_role(label: str) -> bool:
    low = (label or "").lower()
    return any(marker in low for marker in _FEMALE_ROLE_MARKERS)


def _is_male_role(label: str) -> bool:
    low = (label or "").lower()
    return any(marker in low for marker in _MALE_ROLE_MARKERS)


def resolve_collage_ref_order(prompt: str, num_refs: int = 2) -> tuple[int, int]:
    """Order refs for composite sheet: woman/primary left, man right (OpenAI fidelity tip)."""
    if num_refs < 2:
        return 0, 0

    labels = {idx: role_label_for_ref(prompt, idx, num_refs) for idx in range(num_refs)}
    female_idx = next((idx for idx, label in labels.items() if _is_female_role(label)), None)
    male_idx = next((idx for idx, label in labels.items() if _is_male_role(label)), None)

    if female_idx is not None and male_idx is not None and female_idx != male_idx:
        return female_idx, male_idx
    return 0, 1


def compose_collage_reference_sheet(
    left_ref_url: str,
    right_ref_url: str,
) -> str:
    """Side-by-side identity sheet — OpenAI multi-face merge pattern."""
    import base64
    from io import BytesIO

    from PIL import Image

    def _decode_png_data_url(data_url: str) -> Image.Image:
        ref = (data_url or "").strip()
        if not ref.startswith("data:"):
            raise ValueError("collage sheet requires PNG data URL")
        raw = base64.b64decode(ref.split(",", 1)[1], validate=False)
        with Image.open(BytesIO(raw)) as img:
            if img.mode not in ("RGB", "RGBA"):
                return img.convert("RGBA" if "A" in img.getbands() else "RGB")
            return img.copy()

    def _scale_to_max_side(img: Image.Image, max_side: int) -> Image.Image:
        width, height = img.size
        longest = max(width, height)
        if longest <= max_side:
            return img
        scale = max_side / float(longest)
        return img.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )

    left = _scale_to_max_side(_decode_png_data_url(left_ref_url), COLLAGE_SHEET_MAX_FACE_SIDE_PX)
    right = _scale_to_max_side(_decode_png_data_url(right_ref_url), COLLAGE_SHEET_MAX_FACE_SIDE_PX)

    target_height = max(left.height, right.height)
    sheet = Image.new("RGBA", (left.width + COLLAGE_SHEET_GAP_PX + right.width, target_height), (255, 255, 255, 255))
    sheet.paste(left, (0, (target_height - left.height) // 2), left if left.mode == "RGBA" else None)
    sheet.paste(
        right,
        (left.width + COLLAGE_SHEET_GAP_PX, (target_height - right.height) // 2),
        right if right.mode == "RGBA" else None,
    )

    out = BytesIO()
    sheet.convert("RGB").save(out, format="PNG", optimize=True)
    encoded = base64.standard_b64encode(out.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def resolve_collage_openrouter_extensions(model: str) -> dict[str, str]:
    """GPT Image 2: quality=high (input_fidelity unsupported — always high internally)."""
    _ = (model or "").strip()
    return {"quality": "high"}


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


def _collage_composite_binding_lines(left_role: str, right_role: str) -> list[str]:
    return [
        (
            "input_references[0] is a side-by-side identity sheet with exactly TWO faces:\n"
            f"- LEFT half = {left_role}: use ONLY this face wherever this person appears.\n"
            f"- RIGHT half = {right_role}: use ONLY this face wherever this person appears.\n"
            "Do not merge, swap, or invent faces. Exactly 2 people total in the entire collage."
        )
    ]


def build_collage_multi_ref_api_prompt(
    user_prompt: str,
    num_refs: int,
    *,
    composite_sheet: bool = False,
    left_role: str = "Person 1",
    right_role: str = "Person 2",
) -> str:
    """ChatCom-style light prompt: layout brief verbatim + minimal ref binding."""
    prompt = (user_prompt or "").strip()
    count = max(2, min(int(num_refs or 0), 10))

    if composite_sheet and count == 2:
        ref_block = "\n".join(_collage_composite_binding_lines(left_role, right_role))
        people_line = "The identity sheet contains exactly 2 people — LEFT and RIGHT halves."
    else:
        ref_block = "\n".join(_collage_ref_binding_lines(count, prompt))
        people_line = f"There are exactly {count} people in input_references — use ONLY these faces."

    header = (
        f"MULTI-REFERENCE PHOTO COLLAGE ({count} people).\n"
        f"{people_line}\n"
        "STRICTLY FORBIDDEN: third persons, strangers, extra heads/hands, invented partners, "
        "printing reference faces on clothing.\n"
        "Follow EVERY frame/cell in the grid exactly as described below.\n"
        "Clothing, poses, empty frames, and grid structure come from the user brief — "
        "NOT from reference outfits.\n"
        "Preserve facial identity from the assigned references; "
        "do not add under-eye bags, aging, or tired eyes.\n\n"
        "Reference binding:\n"
        f"{ref_block}\n\n"
        "USER COLLAGE BRIEF:\n"
    )
    return append_negative_prompt_directive(
        f"{header}{prompt}",
        negative=_COLLAGE_NEGATIVE_PROMPT,
    )
