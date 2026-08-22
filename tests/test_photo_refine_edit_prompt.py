"""Prompt for «Доработать текстом» — preserve scene, apply edits."""

from services.multi_ref_scene_parser import SceneCharacter, SceneLayout
from services.openrouter_images import build_structured_multi_ref_prompt
from services.photo_edit_session import (
    build_group_refine_user_prompt,
    build_photo_refine_edit_prompt,
    session_has_group_refs,
    save_photo_edit_session,
    reset_photo_edit_sessions_for_tests,
    PhotoEditSession,
)


def test_build_photo_refine_edit_prompt_preserves_reference() -> None:
    prompt = build_photo_refine_edit_prompt("add warm sunset glow")
    assert "preserve the same subjects" in prompt.lower()
    assert "add warm sunset glow" in prompt
    assert "do not regenerate from scratch" in prompt.lower()
    assert "completely new scene" not in prompt.lower()


def test_build_group_refine_user_prompt_keeps_base_scene() -> None:
    combined = build_group_refine_user_prompt(
        "мама, сын и дочка на пляже",
        "сделай маме волосы покороче",
    )
    assert "мама, сын и дочка на пляже" in combined
    assert "сделай маме волосы покороче" in combined
    assert "EDIT REQUEST" in combined
    assert "input_references" in combined


def test_session_has_group_refs() -> None:
    reset_photo_edit_sessions_for_tests()
    assert not session_has_group_refs(None)
    sess = save_photo_edit_session(
        1,
        image_model_id="nano_banana_pro",
        image_model_label="Nano Pro",
        telegram_file_id="AgAC_result",
        group_ref_file_ids=["AgAC_mom", "AgAC_son", "AgAC_daughter"],
        group_base_prompt="семейный портрет",
    )
    assert sess is not None
    assert session_has_group_refs(sess)


def test_structured_group_prompt_includes_targeted_edit_rule() -> None:
    layout = SceneLayout(
        characters=[
            SceneCharacter(
                ref_index=0,
                label="mother",
                placement="center",
                appearance_anchor="adult woman",
            ),
        ],
        scene_description_en="Family on the beach. EDIT REQUEST: shorter hair for mother",
    )
    prompt = build_structured_multi_ref_prompt(
        layout,
        ["adult woman", "boy", "girl"],
    )
    assert "TARGETED EDIT RULE" in prompt
    assert "Do not replace anyone with a new person" in prompt
