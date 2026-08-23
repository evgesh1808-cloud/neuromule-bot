"""Tests for multi-step photo booth collage generation."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from config import Settings
from services.gemini_image_client import GeminiImageResult
from services.photo_collage_multistep import (
    build_multistep_cell_prompt,
    classify_cell_ref_mode,
    finalize_collage_grid_spec,
    generate_collage_multistep,
    is_empty_frame_intent,
    parse_collage_grid_spec,
    resolve_cell_input_references,
    stitch_collage_grid,
)

PHOTO_BOOTH_PROMPT = (
    "Чёрно-белая фотобудка, 2 колонки × 4 ряда. "
    "фото1=девушка, фото2=парень\n"
    "Левая колонка (сверху вниз):\n"
    "— 1 квадрат: девушка крупным планом, улыбка\n"
    "— 2 квадрат: пара смеётся в профиль\n"
    "— 3 квадрат: поцелуй в профиль\n"
    "— 4 квадрат: объятие, оба в камеру\n"
    "Правая колонка (сверху вниз):\n"
    "— 1 квадрат: парень крупным планом в камеру\n"
    "— 2 квадрат: обрезанное плечо, лица нет\n"
    "— 3 квадрат: почти пустой кадр\n"
    "— 4 квадрат: пустой светлый кадр"
)


def _tiny_png(color: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", (32, 32), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_is_empty_frame_intent() -> None:
    assert is_empty_frame_intent("пустой светлый кадр") is True
    assert is_empty_frame_intent("девушка крупным планом") is False


def test_parse_collage_grid_spec_2x4() -> None:
    spec = parse_collage_grid_spec(PHOTO_BOOTH_PROMPT)
    assert spec.cols == 2
    assert spec.rows == 4
    assert len(spec.cells) == 8
    assert spec.cells[0].col == 0 and spec.cells[0].row == 0
    assert "девушка" in spec.cells[0].description.lower()


def test_finalize_replaces_empty_frames() -> None:
    raw = parse_collage_grid_spec(PHOTO_BOOTH_PROMPT)
    spec = finalize_collage_grid_spec(raw, left_role="девушка", right_role="парень")
    for cell in spec.cells:
        assert not is_empty_frame_intent(cell.description)
    assert "пуст" not in spec.cells[7].description.lower()


def test_classify_cell_ref_mode() -> None:
    assert classify_cell_ref_mode("девушка крупным планом", col=0) == "solo_left"
    assert classify_cell_ref_mode("пара смеётся в профиль", col=0) == "both"
    assert classify_cell_ref_mode("парень крупным планом", col=1) == "solo_right"


def test_build_multistep_cell_prompt_identity_anchors() -> None:
    spec = finalize_collage_grid_spec(
        parse_collage_grid_spec(PHOTO_BOOTH_PROMPT),
        left_role="девушка",
        right_role="парень",
    )
    prompt = build_multistep_cell_prompt(
        spec.cells[2],
        left_role="девушка",
        right_role="парень",
        left_face_desc="young woman, oval face, brown eyes",
        right_face_desc="young man, square jaw, dark hair",
        style_prefix="B&W photo booth",
    )
    assert "ONE single photo booth frame ONLY" in prompt
    assert "PROFILE/KISS IDENTITY" in prompt
    assert "input_references[0]" in prompt
    assert "input_references[1]" in prompt
    assert "brown eyes" in prompt


def test_resolve_cell_input_references_solo_uses_one_ref() -> None:
    spec = finalize_collage_grid_spec(
        parse_collage_grid_spec(PHOTO_BOOTH_PROMPT),
        left_role="девушка",
        right_role="парень",
    )
    solo = next(c for c in spec.cells if c.ref_mode == "solo_left")
    refs = resolve_cell_input_references(solo, "left_url", "right_url")
    assert len(refs) == 1


def test_stitch_collage_grid_9_16() -> None:
    cells = {
        (0, 0): _tiny_png((255, 0, 0)),
        (1, 0): _tiny_png((0, 255, 0)),
        (0, 1): _tiny_png((0, 0, 255)),
        (1, 1): _tiny_png((128, 128, 128)),
    }
    stitched = stitch_collage_grid(cells, cols=2, rows=2, canvas_width=540)
    with Image.open(BytesIO(stitched)) as img:
        assert img.width == 540
        assert img.height == 960


@pytest.mark.asyncio
async def test_generate_collage_multistep_stitches_cells() -> None:
    settings = Settings(tg_token="t", openrouter_key="k")
    fake_png = _tiny_png((200, 200, 200))
    captured_refs: list[int] = []

    async def _fake_generate(*_args, input_references=None, **_kwargs) -> GeminiImageResult:
        captured_refs.append(len(input_references or []))
        return GeminiImageResult(data=fake_png)

    with patch(
        "services.photo_collage_multistep.generate_openrouter_image",
        AsyncMock(side_effect=_fake_generate),
    ) as gen:
        result = await generate_collage_multistep(
            settings,
            user_prompt=PHOTO_BOOTH_PROMPT,
            left_ref_url="data:image/png;base64,left",
            right_ref_url="data:image/png;base64,right",
            left_role="девушка",
            right_role="парень",
            left_face_desc="woman face",
            right_face_desc="man face",
        )

    assert result.data is not None
    assert gen.await_count == 8
    assert 1 in captured_refs
    assert 2 in captured_refs
    with Image.open(BytesIO(result.data)) as img:
        assert img.width == 1080
        assert img.height == 1920
