"""
VK-адаптер входящих сообщений (аналог ``vk.ts`` в TypeScript-стеке).

Нормализует vkbottle ``Message`` в структуры Core-роутера; для photo
достаёт URL максимального размера и передаёт его как ``reference_image_url``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.inbound_router import PhotoGenerationRequest, PhotoReference
from services.vk_photo_url import extract_photo_url_from_vk_attachment


@dataclass(frozen=True, slots=True)
class VkInboundMessage:
    user_id: int
    peer_id: int
    text: str
    reference_image_url: str | None = None


def extract_vk_photo_url(message: Any) -> str | None:
    """Максимальный HTTPS-URL из первого photo-вложения сообщения VK."""
    attachments = getattr(message, "attachments", None) or []
    for attachment in attachments:
        url = extract_photo_url_from_vk_attachment(attachment)
        if url:
            return url
    return None


def normalize_vk_message(message: Any) -> VkInboundMessage:
    """Нормализация VK-сообщения: текст + опциональный URL референса."""
    user_id = int(getattr(message, "from_id", 0) or 0)
    peer_id = int(getattr(message, "peer_id", 0) or user_id)
    text = (getattr(message, "text", None) or "").strip()
    photo_url = extract_vk_photo_url(message)
    return VkInboundMessage(
        user_id=user_id,
        peer_id=peer_id,
        text=text,
        reference_image_url=photo_url,
    )


def to_photo_generation_request(
    inbound: VkInboundMessage,
    *,
    image_model_id: str,
    image_model_label: str,
    prompt: str,
    reference_image_url: str | None = None,
) -> PhotoGenerationRequest:
    """Сборка запроса для ``core.inbound_router.route_photo_generation``."""
    ref_url = (reference_image_url or inbound.reference_image_url or "").strip() or None
    photo_ref = PhotoReference(reference_image_url=ref_url) if ref_url else None
    return PhotoGenerationRequest(
        platform="vk",
        user_id=inbound.user_id,
        chat_id=inbound.peer_id,
        prompt=prompt,
        image_model_id=image_model_id,
        image_model_label=image_model_label,
        photo_ref=photo_ref,
    )
