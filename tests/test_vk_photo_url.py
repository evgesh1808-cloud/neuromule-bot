"""Тесты выбора максимального VK photo URL."""

from __future__ import annotations

from services.vk_photo_url import (
    extract_photo_url_from_vk_attachment,
    pick_largest_vk_photo_url,
)


def test_pick_largest_vk_photo_url_by_area() -> None:
    sizes = [
        {"type": "m", "width": 130, "height": 87, "url": "https://cdn/small.jpg"},
        {"type": "w", "width": 2560, "height": 1704, "url": "https://cdn/large.jpg"},
        {"type": "x", "width": 604, "height": 402, "url": "https://cdn/medium.jpg"},
    ]
    assert pick_largest_vk_photo_url(sizes) == "https://cdn/large.jpg"


def test_pick_largest_vk_photo_url_uses_src_field() -> None:
    sizes = [{"type": "z", "width": 1280, "height": 720, "src": "https://cdn/z.webp"}]
    assert pick_largest_vk_photo_url(sizes) == "https://cdn/z.webp"


def test_pick_largest_vk_photo_url_empty() -> None:
    assert pick_largest_vk_photo_url([]) is None
    assert pick_largest_vk_photo_url(None) is None


def test_extract_photo_url_from_vk_attachment_dict() -> None:
    attachment = {
        "type": "photo",
        "photo": {
            "sizes": [
                {"width": 100, "height": 100, "url": "https://cdn/a.jpg"},
                {"width": 800, "height": 600, "url": "https://cdn/b.jpg"},
            ]
        },
    }
    assert extract_photo_url_from_vk_attachment(attachment) == "https://cdn/b.jpg"


def test_extract_photo_url_from_vk_attachment_skips_non_photo() -> None:
    assert extract_photo_url_from_vk_attachment({"type": "doc"}) is None
