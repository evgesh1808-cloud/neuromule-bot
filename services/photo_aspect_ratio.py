"""Константы и нормализация aspect ratio для photo FSM / OpenRouter."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PHOTO_ASPECT_RATIO = "1:1"
PHOTO_ASPECT_RATIOS: frozenset[str] = frozenset({"1:1", "3:4", "4:5", "9:16", "16:9"})

# Канонические значения для ``generate_openrouter_image(..., aspect_ratio=...)``.
ASPECT_RATIO_SQUARE = "1:1"
ASPECT_RATIO_VERTICAL_POST = "3:4"
ASPECT_RATIO_SOCIAL = "4:5"
ASPECT_RATIO_STORIES = "9:16"
ASPECT_RATIO_WIDE = "16:9"

# Модели с inline-меню формата (нормализованные ключи из ``normalize_image_model``).
_ASPECT_RATIO_MENU_MODELS: frozenset[str] = frozenset({"flux_2_pro", "nano_banana_pro"})


@dataclass(frozen=True, slots=True)
class AspectRatioMenuEntry:
    """Одна кнопка inline-меню формата."""

    callback_suffix: str
    value: str
    button_label: str
    description: str


# Суффикс callback ``img_ar:1x1`` → ``1:1`` (двоеточие в ``callback_data`` ломает парсинг).
# На кнопке — короткая подпись (иконка + ratio); пояснение — в тексте сообщения.
ASPECT_RATIO_MENU_ENTRIES: tuple[AspectRatioMenuEntry, ...] = (
    AspectRatioMenuEntry("1x1", ASPECT_RATIO_SQUARE, "▢ 1:1", "квадрат — аватар, пост"),
    AspectRatioMenuEntry("3x4", ASPECT_RATIO_VERTICAL_POST, "▮ 3:4", "вертикальный пост"),
    AspectRatioMenuEntry("4x5", ASPECT_RATIO_SOCIAL, "📷 4:5", "Instagram / лента"),
    AspectRatioMenuEntry("9x16", ASPECT_RATIO_STORIES, "📱 9:16", "Stories, Reels, TikTok"),
    AspectRatioMenuEntry("16x9", ASPECT_RATIO_WIDE, "🖥 16:9", "обложка, презентация, ПК"),
)

_ASPECT_CALLBACK_MAP: dict[str, str] = {
    entry.callback_suffix: entry.value for entry in ASPECT_RATIO_MENU_ENTRIES
}


def aspect_ratio_menu_options() -> tuple[tuple[str, str], ...]:
    """Пары ``(button_label, callback_suffix)`` для inline-клавиатуры."""
    return tuple((entry.button_label, entry.callback_suffix) for entry in ASPECT_RATIO_MENU_ENTRIES)


def format_aspect_ratio_picker_html() -> str:
    """Текст экрана выбора формата: легенда в сообщении, короткие кнопки ниже."""
    lines = ["Выбери <b>формат кадра</b>:\n"]
    for entry in ASPECT_RATIO_MENU_ENTRIES:
        lines.append(f"{entry.button_label} — {entry.description}")
    return "\n".join(lines)


def model_shows_aspect_ratio_menu(model_id: str | None) -> bool:
    """True для Flux 2 Pro / Nano Banana Pro — показываем выбор формата."""
    from services.billing.image_pipeline import normalize_image_model

    return normalize_image_model(model_id or "") in _ASPECT_RATIO_MENU_MODELS


def normalize_photo_aspect_ratio(value: str | None) -> str:
    cleaned = (value or "").strip()
    if cleaned in PHOTO_ASPECT_RATIOS:
        return cleaned
    return DEFAULT_PHOTO_ASPECT_RATIO


def replicate_flux_aspect_ratio(value: str | None) -> str:
    """Строка aspect_ratio для Replicate Flux (``1:1`` … ``16:9``).

    Replicate ``black-forest-labs/flux-schnell`` принимает ``aspect_ratio`` как строку,
    не width/height — конвертация в пиксели не требуется.
    """
    return normalize_photo_aspect_ratio(value)


def openrouter_aspect_ratio(value: str | None) -> str:
    """Чистый параметр ``aspect_ratio`` для ``generate_openrouter_image``."""
    return normalize_photo_aspect_ratio(value)


def aspect_ratio_from_callback_suffix(suffix: str | None) -> str | None:
    """Декодирует ``img_ar:<suffix>`` в каноническое ``1:1`` … ``16:9``."""
    key = (suffix or "").strip().lower()
    mapped = _ASPECT_CALLBACK_MAP.get(key)
    if mapped is not None:
        return mapped
    if key in PHOTO_ASPECT_RATIOS:
        return key
    return None
