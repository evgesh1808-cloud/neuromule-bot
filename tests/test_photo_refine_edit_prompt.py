"""Prompt for «Доработать текстом» — preserve scene, apply edits."""

from services.photo_edit_session import build_photo_refine_edit_prompt


def test_build_photo_refine_edit_prompt_preserves_reference() -> None:
    prompt = build_photo_refine_edit_prompt("add warm sunset glow")
    assert "preserve the same subjects" in prompt.lower()
    assert "add warm sunset glow" in prompt
    assert "do not regenerate from scratch" in prompt.lower()
    assert "completely new scene" not in prompt.lower()
