"""Константы и нормализация aspect ratio для photo FSM / OpenRouter."""

from __future__ import annotations

DEFAULT_PHOTO_ASPECT_RATIO = "1:1"
PHOTO_ASPECT_RATIOS: frozenset[str] = frozenset({"1:1", "3:4", "16:9"})

# Суффикс callback ``img_ar:1x1`` → ``1:1`` (двоеточие в callback_data ломает парсинг).
_ASPECT_CALLBACK_MAP: dict[str, str] = {
    "1x1": "1:1",
    "3x4": "3:4",
    "16x9": "16:9",
}


def normalize_photo_aspect_ratio(value: str | None) -> str:
    cleaned = (value or "").strip()
    if cleaned in PHOTO_ASPECT_RATIOS:
        return cleaned
    return DEFAULT_PHOTO_ASPECT_RATIO


def replicate_flux_aspect_ratio(value: str | None) -> str:
    """Строка aspect_ratio для Replicate Flux (поддерживает ``1:1``, ``3:4``, ``16:9``).

    Replicate ``black-forest-labs/flux-schnell`` принимает ``aspect_ratio`` как строку,
    не width/height — конвертация в пиксели не требуется.
    """
    return normalize_photo_aspect_ratio(value)


def openrouter_aspect_ratio(value: str | None) -> str:
    """Строка aspect_ratio для OpenRouter Images API (``"1:1"``, ``"3:4"``, ``"16:9"``)."""
    return normalize_photo_aspect_ratio(value)


def aspect_ratio_from_callback_suffix(suffix: str | None) -> str | None:
    key = (suffix or "").strip().lower()
    mapped = _ASPECT_CALLBACK_MAP.get(key)
    if mapped is not None:
        return mapped
    if key in PHOTO_ASPECT_RATIOS:
        return key
    return None
