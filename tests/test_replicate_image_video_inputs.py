"""Replicate image-to-video input builder."""

from services.replicate_client import build_image_to_video_inputs


def test_build_image_to_video_inputs_sets_start_image_and_legacy_url() -> None:
    payload = build_image_to_video_inputs(
        prompt="gentle motion",
        image_url="https://cdn.example/photo.jpg",
        aspect_ratio="9:16",
    )
    assert payload["prompt"] == "gentle motion"
    assert payload["start_image"] == "https://cdn.example/photo.jpg"
    assert payload["start_image_url"] == "https://cdn.example/photo.jpg"
    assert payload["aspect_ratio"] == "9:16"
