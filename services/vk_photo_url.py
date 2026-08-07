"""Чистые функции: выбор URL максимального размера из VK photo sizes."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable


@runtime_checkable
class _VkPhotoSize(Protocol):
    url: str | None
    width: int
    height: int


def _size_url(size: Mapping[str, Any] | object) -> str | None:
    if isinstance(size, Mapping):
        raw = size.get("url") or size.get("src")
    else:
        raw = getattr(size, "url", None) or getattr(size, "src", None)
    if not raw:
        return None
    text = str(raw).strip()
    if text.startswith(("http://", "https://")):
        return text
    return None


def _size_dims(size: Mapping[str, Any] | object) -> tuple[int, int]:
    if isinstance(size, Mapping):
        width = int(size.get("width") or 0)
        height = int(size.get("height") or 0)
    else:
        width = int(getattr(size, "width", 0) or 0)
        height = int(getattr(size, "height", 0) or 0)
    return width, height


def pick_largest_vk_photo_url(sizes: list[Any] | tuple[Any, ...] | None) -> str | None:
    """Возвращает HTTPS-URL наибольшего превью (по width * height)."""
    if not sizes:
        return None

    best_url: str | None = None
    best_area = -1
    best_width = -1

    for size in sizes:
        url = _size_url(size)
        if not url:
            continue
        width, height = _size_dims(size)
        area = width * height
        if area > best_area or (area == best_area and width > best_width):
            best_area = area
            best_width = width
            best_url = url

    return best_url


def extract_photo_url_from_vk_attachment(attachment: Any) -> str | None:
    """Из одного VK-вложения типа ``photo`` достаёт URL максимального размера."""
    att_type = getattr(attachment, "type", None)
    if att_type is None and isinstance(attachment, Mapping):
        att_type = attachment.get("type")
    if att_type != "photo":
        return None

    photo = getattr(attachment, "photo", None)
    if photo is None and isinstance(attachment, Mapping):
        photo = attachment.get("photo")
    if photo is None:
        return None

    sizes = getattr(photo, "sizes", None) or getattr(photo, "images", None)
    if sizes is None and isinstance(photo, Mapping):
        sizes = photo.get("sizes") or photo.get("images")
    return pick_largest_vk_photo_url(sizes or [])
