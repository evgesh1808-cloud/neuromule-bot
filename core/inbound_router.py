"""
Core-роутер входящих сообщений с фото для i2i.

Telegram передаёт ``telegram_file_id``; VK — прямой ``reference_image_url``
(максимальный size из attachment). Оба варианта сводятся к ``PhotoReference``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from config import Settings
from services.photo_aspect_ratio import normalize_photo_aspect_ratio
from services.use_cases.photo_generation_turn import PhotoGenResult, run_photo_generation_turn

if TYPE_CHECKING:
    from aiogram import Bot


PlatformId = Literal["telegram", "vk"]


@dataclass(frozen=True, slots=True)
class PhotoReference:
    """Источник референса для image-to-image."""

    telegram_file_id: str | None = None
    reference_image_url: str | None = None
    reference_image_bytes: bytes | None = None
    reference_mime: str = "image/jpeg"

    def __post_init__(self) -> None:
        tg = (self.telegram_file_id or "").strip() or None
        url = (self.reference_image_url or "").strip() or None
        raw = self.reference_image_bytes
        if isinstance(raw, memoryview):
            raw = raw.tobytes()
        elif isinstance(raw, bytearray):
            raw = bytes(raw)
        elif raw is not None and not isinstance(raw, bytes):
            raise TypeError("reference_image_bytes must be bytes")
        sources = sum(x is not None for x in (tg, url, raw))
        if sources > 1:
            raise ValueError(
                "PhotoReference: only one of telegram_file_id, reference_image_url, reference_image_bytes"
            )
        object.__setattr__(self, "telegram_file_id", tg)
        object.__setattr__(self, "reference_image_url", url)
        object.__setattr__(self, "reference_image_bytes", raw)

    @property
    def has_reference(self) -> bool:
        return bool(
            self.telegram_file_id or self.reference_image_url or self.reference_image_bytes
        )


@dataclass(frozen=True, slots=True)
class PhotoGenerationRequest:
    platform: PlatformId
    user_id: int
    chat_id: int
    prompt: str
    image_model_id: str
    image_model_label: str
    photo_ref: PhotoReference | None = None
    aspect_ratio: str | None = None


async def route_photo_generation(
    settings: Settings,
    request: PhotoGenerationRequest,
    *,
    bot: "Bot | None" = None,
) -> PhotoGenResult:
    """
    Единая точка маршрутизации i2i/t2i в ``run_photo_generation_turn``.

    ``bot`` обязателен для Telegram (скачивание file_id); для VK достаточно URL.
    """
    ref = request.photo_ref or PhotoReference()
    if request.platform == "telegram" and ref.telegram_file_id and bot is None:
        raise ValueError("route_photo_generation: bot required for Telegram file_id")

    return await run_photo_generation_turn(
        settings,
        bot,  # type: ignore[arg-type]
        request.chat_id,
        request.user_id,
        request.image_model_id,
        request.image_model_label,
        request.prompt,
        telegram_file_id=ref.telegram_file_id,
        reference_image_url=ref.reference_image_url,
        reference_image_bytes=ref.reference_image_bytes,
        reference_mime=ref.reference_mime,
        aspect_ratio=normalize_photo_aspect_ratio(request.aspect_ratio),
    )
