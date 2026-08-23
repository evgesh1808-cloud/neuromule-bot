"""Multi-step collage: one API call per grid cell, then stitch (ChatCom-style layout fidelity)."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from config import Settings
from services.api_resilience import ExternalApiError
from services.gemini_image_client import GeminiImageResult
from services.openrouter_images import (
    MULTI_REF_COLLAGE_PRIMARY_MODEL,
    append_negative_prompt_directive,
    generate_openrouter_image,
    openrouter_input_reference,
)
from services.photo_collage_mode import resolve_collage_openrouter_extensions

logger = logging.getLogger(__name__)

COLLAGE_CELL_ASPECT_RATIO = "1:1"
COLLAGE_CELL_GAP_PX = 14
COLLAGE_CELL_CORNER_RADIUS_PX = 18
COLLAGE_MULTISTEP_MAX_CELLS = 8
COLLAGE_CELL_CONCURRENCY = 2
COLLAGE_FINAL_WIDTH_PX = 1080

CellRefMode = Literal["solo_left", "solo_right", "both"]

_GRID_SIZE_RE = re.compile(
    r"2\s*[×x]\s*(\d+)|(\d+)\s*колонк[^\n]{0,40}?[×x]\s*(\d+)\s*ряд",
    re.IGNORECASE,
)
_LEFT_COLUMN_RE = re.compile(
    r"левая колонка.*?(?=правая колонка|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_RIGHT_COLUMN_RE = re.compile(
    r"правая колонка.*?(?=\Z)",
    re.IGNORECASE | re.DOTALL,
)
_CELL_LINE_RE = re.compile(
    r"[-—]?\s*(\d+)\s*квадрат[:\s—-]*(.*?)(?=[-—]\s*\d+\s*квадрат|\Z)",
    re.IGNORECASE | re.DOTALL,
)

_COUPLE_MARKERS = frozenset(
    {
        "пара",
        "couple",
        "both",
        "оба",
        "обним",
        "hug",
        "kiss",
        "поцел",
        "вместе",
        "together",
        "profile facing",
        "в профиль",
        "профил",
        "profile",
        "смеёт",
        "laugh",
        "smil",
    }
)
_SOLO_MARKERS = frozenset(
    {
        "крупным планом",
        "close-up",
        "close up",
        "portrait",
        "solo",
        "alone",
        "only",
        "только",
        "один",
        "одна",
    }
)
_EMPTY_FRAME_MARKERS = frozenset(
    {
        "пуст",
        "empty",
        "negative space",
        "minimal shoulder",
        "лица нет",
        "no face",
        "almost empty",
        "nearly empty",
        "обрезан",
        "shoulder edge",
        "tiny shirt",
        "blank frame",
        "white frame",
    }
)

_CELL_NEGATIVE_PROMPT = (
    "grid layout, contact sheet, multiple panels, collage, split image, diptych, "
    "third person, stranger, invented face, blended faces, swapped identities, "
    "face on clothing, empty frame, blank frame, white void, missing subject, "
    "under-eye bags, dark circles, aged skin, tired eyes"
)


@dataclass(frozen=True, slots=True)
class CollageCellSpec:
    col: int
    row: int
    description: str
    ref_mode: CellRefMode


@dataclass(frozen=True, slots=True)
class CollageGridSpec:
    cols: int
    rows: int
    cells: tuple[CollageCellSpec, ...]
    style_prefix: str


def is_empty_frame_intent(description: str) -> bool:
    low = (description or "").strip().lower()
    if not low:
        return True
    return any(marker in low for marker in _EMPTY_FRAME_MARKERS)


def _detect_grid_size(prompt: str) -> tuple[int, int]:
    low = (prompt or "").lower()
    match = _GRID_SIZE_RE.search(low)
    if match:
        if match.group(1):
            rows = int(match.group(1))
            return 2, rows
        if match.group(2) and match.group(3):
            return int(match.group(2)), int(match.group(3))
    if "2x2" in low or "2×2" in low:
        return 2, 2
    return 2, 4


def _parse_column_cells(section: str, rows: int) -> dict[int, str]:
    found: dict[int, str] = {}
    for match in _CELL_LINE_RE.finditer(section or ""):
        row_num = int(match.group(1))
        desc = (match.group(2) or "").strip()
        if not desc or row_num < 1 or row_num > rows:
            continue
        found[row_num - 1] = desc[:800]
    return found


def _extract_style_prefix(prompt: str) -> str:
    text = (prompt or "").strip()
    cut = _LEFT_COLUMN_RE.search(text)
    if cut:
        return text[: cut.start()].strip()[:1200]
    return text[:600]


def _default_cell_description(col: int, row: int, left_role: str, right_role: str) -> str:
    """Meaningful photo-booth frames — no empty panels."""
    left_defaults = (
        f"{left_role} close-up portrait, shoulders up, warm natural smile, looking at camera.",
        f"Couple laughing in profile facing each other, {left_role} on the left, candid joy.",
        f"Profile kiss, {left_role} and {right_role}, eyes closed, intimate close framing.",
        f"Couple hugging, both smiling warmly at camera, relaxed happy pose.",
    )
    right_defaults = (
        f"{right_role} close-up portrait, shoulders up, confident smile, looking at camera.",
        f"Couple laughing in profile, {right_role} on the right, emotional candid moment.",
        f"Profile kiss close-up, {right_role} and {left_role}, preserve exact profile features.",
        f"Couple embracing, {right_role} and {left_role} facing camera, genuine smiles.",
    )
    defaults = left_defaults if col == 0 else right_defaults
    return defaults[row] if row < len(defaults) else defaults[-1]


def classify_cell_ref_mode(description: str, col: int) -> CellRefMode:
    """Pick 1 or 2 references per cell for maximum likeness."""
    low = (description or "").lower()
    has_couple = any(marker in low for marker in _COUPLE_MARKERS)
    has_solo = any(marker in low for marker in _SOLO_MARKERS)

    if has_couple and not has_solo:
        return "both"
    if has_solo and not has_couple:
        return "solo_left" if col == 0 else "solo_right"
    if has_couple:
        return "both"
    return "solo_left" if col == 0 else "solo_right"


def finalize_collage_grid_spec(
    spec: CollageGridSpec,
    *,
    left_role: str,
    right_role: str,
) -> CollageGridSpec:
    """Ensure every cell has a meaningful description and ref mode — never empty frames."""
    cells: list[CollageCellSpec] = []
    for row in range(spec.rows):
        for col in range(spec.cols):
            existing = next((c for c in spec.cells if c.col == col and c.row == row), None)
            desc = (existing.description if existing else "").strip()
            if not desc or is_empty_frame_intent(desc):
                desc = _default_cell_description(col, row, left_role, right_role)
            ref_mode = classify_cell_ref_mode(desc, col)
            cells.append(CollageCellSpec(col=col, row=row, description=desc, ref_mode=ref_mode))
    return CollageGridSpec(
        cols=spec.cols,
        rows=spec.rows,
        cells=tuple(cells),
        style_prefix=spec.style_prefix,
    )


def parse_collage_grid_spec(user_prompt: str) -> CollageGridSpec:
    """Parse 2×N photo booth grid from Russian/English prompt."""
    prompt = (user_prompt or "").strip()
    cols, rows = _detect_grid_size(prompt)
    style = _extract_style_prefix(prompt)

    left_match = _LEFT_COLUMN_RE.search(prompt)
    right_match = _RIGHT_COLUMN_RE.search(prompt)
    left_cells = _parse_column_cells(left_match.group(0) if left_match else "", rows)
    right_cells = _parse_column_cells(right_match.group(0) if right_match else "", rows)

    specs: list[CollageCellSpec] = []
    for row in range(rows):
        for col in range(cols):
            raw = left_cells.get(row) if col == 0 else right_cells.get(row)
            desc = (raw or "").strip()
            ref_mode: CellRefMode = classify_cell_ref_mode(desc, col) if desc else (
                "solo_left" if col == 0 else "solo_right"
            )
            specs.append(CollageCellSpec(col=col, row=row, description=desc, ref_mode=ref_mode))

    return CollageGridSpec(cols=cols, rows=rows, cells=tuple(specs), style_prefix=style)


def _identity_anchor(role: str, face_desc: str, ref_idx: int) -> str:
    anchor = (face_desc or "").strip()
    if anchor:
        return (
            f"{role} → input_references[{ref_idx}]: {anchor}. "
            f"Preserve exact eye shape, nose bridge, jawline, lip proportions, skin tone, "
            f"and apparent age from input_references[{ref_idx}]. "
            f"Do not add under-eye bags, dark circles, or aging not in the reference."
        )
    return (
        f"{role} → input_references[{ref_idx}]: MUST match reference face exactly — "
        f"identical bone structure, features, and skin. No similar-looking stranger."
    )


def _profile_kiss_block(description: str) -> str:
    low = (description or "").lower()
    if not any(m in low for m in ("поцел", "kiss", "profile", "профил")):
        return ""
    return (
        "PROFILE/KISS IDENTITY: In side profile or with eyes closed, still preserve exact "
        "nose bridge, lip contour, chin, and jawline from each person's reference. "
        "No generic silhouettes — these are specific real faces.\n\n"
    )


def build_multistep_cell_prompt(
    cell: CollageCellSpec,
    *,
    left_role: str,
    right_role: str,
    left_face_desc: str,
    right_face_desc: str,
    style_prefix: str,
) -> str:
    """Single-frame prompt with dual-ref identity anchors."""
    style = (style_prefix or "").strip()
    style_block = f"Global style: {style}\n" if style else ""
    profile_block = _profile_kiss_block(cell.description)

    if cell.ref_mode == "solo_left":
        ref_block = _identity_anchor(left_role, left_face_desc, 0)
        people_rule = (
            f"Exactly ONE person in frame: {left_role} only. "
            f"Use input_references[0] exclusively. No partner, no stranger."
        )
    elif cell.ref_mode == "solo_right":
        ref_block = _identity_anchor(right_role, right_face_desc, 0)
        people_rule = (
            f"Exactly ONE person in frame: {right_role} only. "
            f"Use input_references[0] exclusively. No partner, no stranger."
        )
    else:
        ref_block = "\n".join(
            [
                _identity_anchor(left_role, left_face_desc, 0),
                _identity_anchor(right_role, right_face_desc, 1),
            ]
        )
        people_rule = (
            f"Exactly TWO people: {left_role} and {right_role}. "
            "Use input_references[0] and input_references[1] respectively. "
            "STRICTLY FORBIDDEN: third person, stranger, face swap, blended identities."
        )

    base = (
        "CRITICAL: Generate ONE single photo booth frame ONLY.\n"
        "NOT a grid. NOT multiple panels. NOT a contact sheet. NOT a collage layout.\n"
        "Exactly one square photograph filling the entire canvas edge-to-edge.\n\n"
        f"{people_rule}\n"
        "Clothing and pose from frame description — NOT from reference outfits.\n\n"
        f"Identity binding:\n{ref_block}\n\n"
        f"{profile_block}"
        f"{style_block}"
        f"THIS FRAME ONLY: {cell.description.strip()}\n"
        "Black and white studio photo booth aesthetic unless style says otherwise."
    )
    return append_negative_prompt_directive(base, negative=_CELL_NEGATIVE_PROMPT)


def resolve_cell_input_references(
    cell: CollageCellSpec,
    left_ref_url: str,
    right_ref_url: str,
) -> list[dict]:
    if cell.ref_mode == "solo_left":
        return [openrouter_input_reference(left_ref_url)]
    if cell.ref_mode == "solo_right":
        return [openrouter_input_reference(right_ref_url)]
    return [
        openrouter_input_reference(left_ref_url),
        openrouter_input_reference(right_ref_url),
    ]


def _round_corners(img: "Image.Image", radius: int) -> "Image.Image":
    from PIL import Image, ImageDraw

    if radius <= 0:
        return img
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, img.width, img.height), radius=radius, fill=255)
    output = Image.new("RGBA", img.size, (255, 255, 255, 255))
    output.paste(img, mask=mask)
    return output.convert("RGB")


def stitch_collage_grid(
    cell_images: dict[tuple[int, int], bytes],
    *,
    cols: int,
    rows: int,
    gap_px: int = COLLAGE_CELL_GAP_PX,
    corner_radius: int = COLLAGE_CELL_CORNER_RADIUS_PX,
    canvas_width: int = COLLAGE_FINAL_WIDTH_PX,
) -> bytes:
    """Compose grid from per-cell PNG bytes."""
    from PIL import Image

    expected = cols * rows
    if len(cell_images) < expected:
        missing = [
            (c, r)
            for r in range(rows)
            for c in range(cols)
            if (c, r) not in cell_images
        ]
        raise ValueError(f"collage stitch missing cells: {missing}")

    canvas_height = int(canvas_width * 16 / 9)
    inner_w = canvas_width - gap_px * (cols + 1)
    inner_h = canvas_height - gap_px * (rows + 1)
    cell_w = max(1, inner_w // cols)
    cell_h = max(1, inner_h // rows)

    canvas = Image.new("RGB", (canvas_width, canvas_height), (245, 245, 242))

    for row in range(rows):
        for col in range(cols):
            raw = cell_images[(col, row)]
            with Image.open(BytesIO(raw)) as img:
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGB")
                else:
                    img = img.convert("RGB")
                fitted = img.resize((cell_w, cell_h), Image.Resampling.LANCZOS)
                fitted = _round_corners(fitted, corner_radius)
                x = gap_px + col * (cell_w + gap_px)
                y = gap_px + row * (cell_h + gap_px)
                canvas.paste(fitted, (x, y))

    out = BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


async def _result_to_png_bytes(settings: Settings, result: GeminiImageResult) -> bytes:
    if result.data:
        return result.data
    url = (result.url or "").strip()
    if not url:
        raise ExternalApiError("OpenRouter", "empty collage cell result")
    from services.openrouter_images import ensure_png_reference_data_url

    data_url = await ensure_png_reference_data_url(url)
    return base64.b64decode(data_url.split(",", 1)[1], validate=False)


async def generate_collage_multistep(
    settings: Settings,
    *,
    user_prompt: str,
    left_ref_url: str,
    right_ref_url: str,
    left_role: str,
    right_role: str,
    left_face_desc: str = "",
    right_face_desc: str = "",
    model: str = MULTI_REF_COLLAGE_PRIMARY_MODEL,
    timeout_sec: float = 180.0,
) -> GeminiImageResult:
    """Generate each grid cell separately with dual-ref identity, stitch into 9:16."""
    raw_spec = parse_collage_grid_spec(user_prompt)
    spec = finalize_collage_grid_spec(raw_spec, left_role=left_role, right_role=right_role)
    if len(spec.cells) > COLLAGE_MULTISTEP_MAX_CELLS:
        raise ExternalApiError("OpenRouter", "collage grid too large for multistep")

    model_id = (model or MULTI_REF_COLLAGE_PRIMARY_MODEL).strip()
    extensions = resolve_collage_openrouter_extensions(model_id)
    sem = asyncio.Semaphore(COLLAGE_CELL_CONCURRENCY)

    async def _gen_cell(cell: CollageCellSpec) -> tuple[tuple[int, int], bytes]:
        cell_prompt = build_multistep_cell_prompt(
            cell,
            left_role=left_role,
            right_role=right_role,
            left_face_desc=left_face_desc,
            right_face_desc=right_face_desc,
            style_prefix=spec.style_prefix,
        )
        refs = resolve_cell_input_references(cell, left_ref_url, right_ref_url)
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                async with sem:
                    result = await generate_openrouter_image(
                        settings,
                        model=model_id,
                        prompt=cell_prompt,
                        aspect_ratio=COLLAGE_CELL_ASPECT_RATIO,
                        input_references=refs,
                        body_extensions=extensions,
                        timeout_sec=timeout_sec,
                    )
                png_bytes = await _result_to_png_bytes(settings, result)
                return (cell.col, cell.row), png_bytes
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "collage cell col=%s row=%s attempt=%s failed: %s",
                    cell.col,
                    cell.row,
                    attempt + 1,
                    exc,
                )
        assert last_exc is not None
        raise last_exc

    logger.info(
        "collage multistep: grid=%sx%s cells=%s model=%s dual_ref=1",
        spec.cols,
        spec.rows,
        len(spec.cells),
        model_id,
    )

    pairs = await asyncio.gather(*(_gen_cell(cell) for cell in spec.cells))
    cell_map = dict(pairs)
    stitched = stitch_collage_grid(
        cell_map,
        cols=spec.cols,
        rows=spec.rows,
    )
    return GeminiImageResult(data=stitched)
