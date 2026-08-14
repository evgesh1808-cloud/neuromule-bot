"""OpenRouter Images API — общий T2I/I2I клиент для NeuroMule."""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import httpx

from config import Settings
from services.api_resilience import ExternalApiError, clip_error_text
from services.gemini_image_client import GeminiImageResult

logger = logging.getLogger(__name__)

OPENROUTER_IMAGES_URL = "https://openrouter.ai/api/v1/images"
# Replicate fallback (не OpenRouter Images API).
REPLICATE_FLUX_SCHNELL_MODEL = "black-forest-labs/flux-schnell"
# OpenRouter Images: flux-schnell снят с каталога OR → flux.2-pro.
OPENROUTER_FLUX_PAID_MODEL = "black-forest-labs/flux.2-pro"
# Nano Banana 2 / fallback Google T2I/I2I на OR Images.
OPENROUTER_NANO_BANANA2_MODEL = "google/gemini-3.1-flash-image-preview"
OPENROUTER_NANO_BANANA_PRO_MODEL = "google/gemini-3-pro-image"
OPENROUTER_GPT_IMAGE2_MODEL = "openai/gpt-image-2"
# Backward-compatible alias (tests / старые импорты).
OPENROUTER_FLUX_SCHNELL_MODEL = OPENROUTER_FLUX_PAID_MODEL
DEFAULT_OPENROUTER_IMAGES_TIMEOUT_SEC = 180.0
REFERENCE_QUALITY_SUFFIX = (
    ", soft gentle smile, perfect detailed eyes with bright reflections, "
    "flawless glowing skin, professional editorial portrait, look sharp and gorgeous, "
    "highly detailed face"
)
IDENTITY_NEGATIVE_PROMPT = (
    "ugly, deformed, tired face, dark circles under eyes, puffy face, "
    "asymmetrical eyes, bad skin, old, expressionless, distorted lips"
)
FACE_DESCRIBE_VISION_MODEL = "google/gemini-2.5-flash"
FACE_DESCRIBE_SYSTEM_PROMPT = (
    "Опиши лицо на фото детально за 15 слов для генератора картинок."
)

# Меню NeuroMule → официальный slug OpenRouter Images.
OPENROUTER_MODEL_BY_MENU_KEY: dict[str, str] = {
    "flux_schnell": OPENROUTER_FLUX_PAID_MODEL,
    "dalle_3": OPENROUTER_GPT_IMAGE2_MODEL,
    "nano_banana2": OPENROUTER_NANO_BANANA2_MODEL,
    "nano_banana_pro": OPENROUTER_NANO_BANANA_PRO_MODEL,
}

# Модели с i2i через стандартный input_references (type image_url).
MODELS_WITH_IMAGE_REFERENCE: frozenset[str] = frozenset(
    {
        OPENROUTER_FLUX_PAID_MODEL,
        OPENROUTER_NANO_BANANA_PRO_MODEL,
        OPENROUTER_NANO_BANANA2_MODEL,
    }
)

# Backward-compatible alias (старые импорты / тесты).
MODELS_WITH_WEIGHTED_IDENTITY_REFERENCE = MODELS_WITH_IMAGE_REFERENCE
IDENTITY_REFERENCE_WEIGHT = 0.72

_IDENTITY_STYLE_TEMPLATE = (
    "{prompt}. Generate a new scene matching the description. "
    "Preserve the subject's facial identity, bone structure, skin tone, and "
    "distinctive features from the reference exactly — new background and styling only."
)


def openrouter_images_configured(settings: Settings) -> bool:
    return bool((settings.openrouter_key or "").strip())


def resolve_openrouter_model_for_menu_key(model_key: str) -> str:
    """Ключ меню/биллинга → slug OpenRouter Images."""
    normalized = (model_key or "").strip().lower().replace("-", "_")
    slug = OPENROUTER_MODEL_BY_MENU_KEY.get(normalized)
    if not slug:
        raise ExternalApiError("OpenRouter", f"unknown image model key: {model_key}")
    return slug


def format_identity_photo_prompt(user_prompt: str) -> str:
    """Текст пользователя + шаблон удержания биометрии лица (без init_images)."""
    cleaned = (user_prompt or "").strip() or "Professional portrait photo"
    return _IDENTITY_STYLE_TEMPLATE.format(prompt=cleaned)


def append_reference_quality_modifiers(user_prompt: str) -> str:
    """Скрытые модификаторы качества при i2i (референс лица)."""
    base = (user_prompt or "").strip() or "Professional portrait photo"
    if base.endswith(REFERENCE_QUALITY_SUFFIX):
        return base
    return f"{base}{REFERENCE_QUALITY_SUFFIX}"


def append_negative_prompt_directive(
    user_prompt: str,
    *,
    negative: str = IDENTITY_NEGATIVE_PROMPT,
) -> str:
    """Негатив в текст prompt — OpenRouter Images API не принимает negative_prompt в JSON."""
    base = (user_prompt or "").strip() or "Professional portrait photo"
    directive = f" [Negative prompt: {(negative or '').strip()}]"
    if directive in base:
        return base
    return f"{base}{directive}"


def openrouter_input_reference(image_url: str) -> dict[str, Any]:
    """Стандартная i2i-ссылка OpenRouter Images API (type image_url)."""
    url = (image_url or "").strip()
    if not url:
        raise ExternalApiError("OpenRouter", "empty input reference URL")
    return {"type": "image_url", "image_url": {"url": url}}


def openrouter_identity_reference(image_url: str, **_: Any) -> dict[str, Any]:
    """Backward-compatible alias → ``openrouter_input_reference``."""
    return openrouter_input_reference(image_url)


def model_uses_image_reference(model: str) -> bool:
    return (model or "").strip() in MODELS_WITH_IMAGE_REFERENCE


def model_uses_weighted_identity_reference(model: str) -> bool:
    return model_uses_image_reference(model)


def model_uses_face_description_prompt(model: str) -> bool:
    return (model or "").strip() == OPENROUTER_GPT_IMAGE2_MODEL


def append_face_description_to_prompt(user_prompt: str, face_description: str) -> str:
    base = (user_prompt or "").strip() or "Professional portrait photo"
    face = (face_description or "").strip()
    if not face:
        return base
    return f"{base}. Subject face: {face}"


async def resolve_openrouter_reference_url(
    *,
    bot: Any | None,
    file_id: str | None,
    reference_image_url: str | None = None,
    reference_image_bytes: bytes | None = None,
    reference_mime: str = "image/jpeg",
) -> str | None:
    """
    URL для ``input_references`` OpenRouter Images.

    Приоритет: публичный http(s) → Telegram file URL (лёгкий payload) → data URL (VK/bytes).
    """
    fid = (file_id or "").strip()
    ref_url = (reference_image_url or "").strip()

    if ref_url.startswith(("http://", "https://")):
        return ref_url

    if fid and bot is not None:
        from services.replicate_client import telegram_photo_download_url

        return await telegram_photo_download_url(bot, fid)

    if reference_image_bytes:
        raw = bytes(reference_image_bytes)
        if not raw:
            raise ExternalApiError("OpenRouter", "empty reference image bytes")
        mime = (reference_mime or "image/jpeg").strip() or "image/jpeg"
        encoded = base64.standard_b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    if ref_url.startswith("data:"):
        return ref_url

    if not fid and not ref_url:
        return None

    raise ExternalApiError("OpenRouter", "reference image URL could not be resolved")


def _mime_from_reference_url(url: str, *, default: str = "image/jpeg") -> str:
    low = (url or "").lower().split("?", 1)[0]
    if low.endswith(".png"):
        return "image/png"
    if low.endswith(".webp"):
        return "image/webp"
    if low.startswith("data:"):
        header = low.split(",", 1)[0]
        if "image/png" in header:
            return "image/png"
        if "image/webp" in header:
            return "image/webp"
    return default


async def reference_url_to_data_url(reference_url: str) -> str:
    """Любой референс → data URL для vision (Gemini не тянет Telegram URL с OR-серверов)."""
    ref = (reference_url or "").strip()
    if not ref:
        raise ExternalApiError("OpenRouter", "empty reference for data URL conversion")
    if ref.startswith("data:"):
        return ref

    if not ref.startswith(("http://", "https://")):
        raise ExternalApiError("OpenRouter", "reference must be http(s) or data URL")

    from services.streaming_download import stream_download_to_bytes

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        data = await stream_download_to_bytes(client, ref, source="face_desc_ref")
    if not data:
        raise ExternalApiError("OpenRouter", "failed to download reference for face description")

    mime = _mime_from_reference_url(ref)
    encoded = base64.standard_b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


async def describe_reference_face_for_prompt(
    settings: Settings,
    reference_image_url: str,
) -> str:
    """GPT Image 2: vision-описание лица через google/gemini-2.5-flash (data URL, не Telegram https)."""
    from services.ai_text import ask_ai_messages

    image_data_url = await reference_url_to_data_url(reference_image_url)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": FACE_DESCRIBE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": FACE_DESCRIBE_SYSTEM_PROMPT},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        },
    ]
    try:
        completion = await ask_ai_messages(
            settings,
            messages,
            models=[FACE_DESCRIBE_VISION_MODEL],
            max_tokens=80,
            timeout=45.0,
        )
    except Exception as exc:
        logger.exception("face description via %s failed", FACE_DESCRIBE_VISION_MODEL)
        raise ExternalApiError("OpenRouter", clip_error_text(exc)) from exc

    description = (completion.content or "").strip()
    if not description:
        raise ExternalApiError("OpenRouter", "face description empty")
    logger.info("face description len=%s model=%s", len(description), FACE_DESCRIBE_VISION_MODEL)
    return description


async def resolve_openrouter_photo_prompt_and_refs(
    settings: Settings,
    *,
    model: str,
    user_prompt: str,
    reference_data_url: str | None,
) -> tuple[str, list[dict[str, Any]] | None]:
    """Промпт и input_references с учётом возможностей конкретной Images-модели."""
    cleaned = (user_prompt or "").strip() or "Professional portrait photo"
    ref = (reference_data_url or "").strip()
    if not ref:
        return cleaned, None

    cleaned = append_reference_quality_modifiers(cleaned)
    cleaned = append_negative_prompt_directive(cleaned)

    model_id = (model or "").strip()
    if model_uses_face_description_prompt(model_id):
        face_desc = await describe_reference_face_for_prompt(settings, ref)
        return append_face_description_to_prompt(cleaned, face_desc), None

    if model_uses_image_reference(model_id):
        return cleaned, [openrouter_input_reference(ref)]

    return cleaned, None


def parse_openrouter_image_payload(payload: dict[str, Any]) -> GeminiImageResult:
    """``data[0].url`` / ``data[0].b64_json`` (и плоский ``data.url``) → результат."""
    data = payload.get("data")
    if isinstance(data, dict):
        item = data
    elif isinstance(data, list) and data:
        item = data[0]
        if not isinstance(item, dict):
            raise RuntimeError("OpenRouter images: data[0] is not an object")
    else:
        raise RuntimeError("OpenRouter images: empty data")

    final_url = item.get("url")
    if isinstance(final_url, str) and final_url.strip():
        return GeminiImageResult(url=final_url.strip())

    b64_raw = item.get("b64_json")
    if isinstance(b64_raw, str) and b64_raw.strip():
        raw = b64_raw.strip()
        if raw.startswith("data:") and "," in raw:
            raw = raw.split(",", 1)[1]
        try:
            image_bytes = base64.b64decode(raw, validate=False)
        except Exception as exc:
            raise RuntimeError("OpenRouter images: invalid b64_json") from exc
        if not image_bytes:
            raise RuntimeError("OpenRouter images: empty b64_json")
        return GeminiImageResult(data=image_bytes)

    raise RuntimeError("OpenRouter images: neither url nor b64_json")


# Backward-compatible alias for internal call-sites.
_parse_openrouter_image_payload = parse_openrouter_image_payload


async def generate_openrouter_image(
    settings: Settings,
    *,
    model: str,
    prompt: str,
    aspect_ratio: str = "1:1",
    input_references: list[dict[str, Any]] | None = None,
    resolution: str | None = None,
    timeout_sec: float = DEFAULT_OPENROUTER_IMAGES_TIMEOUT_SEC,
) -> GeminiImageResult:
    """POST ``/api/v1/images`` → ``GeminiImageResult``; ошибки → ``ExternalApiError``."""
    if not openrouter_images_configured(settings):
        raise ExternalApiError("OpenRouter", "OPENROUTER_API_KEY не задан")

    cleaned_prompt = (prompt or "").strip()
    if not cleaned_prompt:
        raise ExternalApiError("OpenRouter", "empty prompt")

    model_id = (model or "").strip()
    if not model_id:
        raise ExternalApiError("OpenRouter", "empty model")

    body: dict[str, Any] = {
        "model": model_id,
        "aspect_ratio": aspect_ratio,
        "prompt": cleaned_prompt,
    }
    if input_references:
        body["input_references"] = input_references
    if resolution:
        body["resolution"] = resolution

    api_key = (settings.openrouter_key or "").strip()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        from services.openrouter_http import get_openrouter_http_client

        client = await get_openrouter_http_client(settings)
        async with asyncio.timeout(timeout_sec):
            response = await client.post(
                OPENROUTER_IMAGES_URL,
                headers=headers,
                json=body,
                timeout=httpx.Timeout(timeout_sec, connect=30.0),
            )
    except TimeoutError as exc:
        raise ExternalApiError("OpenRouter", "timeout") from exc
    except httpx.HTTPError as exc:
        raise ExternalApiError("OpenRouter", clip_error_text(exc)) from exc

    if response.status_code >= 400:
        response_text = response.text or ""
        logger.error(
            "OpenRouter Images HTTP %s model=%s aspect_ratio=%s refs=%s response=%s",
            response.status_code,
            model_id,
            aspect_ratio,
            len(input_references or []),
            response_text,
        )
        snippet = clip_error_text(response_text[:4000] or f"HTTP {response.status_code}")
        raise ExternalApiError("OpenRouter", f"HTTP {response.status_code}: {snippet}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise ExternalApiError("OpenRouter", "invalid JSON response") from exc

    if not isinstance(payload, dict):
        raise ExternalApiError("OpenRouter", "response is not a JSON object")

    try:
        return parse_openrouter_image_payload(payload)
    except RuntimeError as exc:
        raise ExternalApiError("OpenRouter", clip_error_text(exc)) from exc


async def upscale_openrouter_image_url(
    settings: Settings,
    image_url: str,
    *,
    scale_value: int,
) -> str:
    """Upscale x2/x4 через OpenRouter Images (референс + resolution tier)."""
    src = (image_url or "").strip()
    if not src.startswith(("http://", "https://")):
        raise ExternalApiError("OpenRouter", "upscale requires http(s) image URL")

    scale = int(scale_value)
    if scale not in (2, 4):
        raise ExternalApiError("OpenRouter", f"unsupported scale_value={scale}")

    resolution = "2K" if scale == 2 else "4K"
    result = await generate_openrouter_image(
        settings,
        model=OPENROUTER_FLUX_PAID_MODEL,
        prompt=(
            "Enhance resolution, sharpness, and fine detail. "
            "Preserve composition, subjects, colors, and facial identity exactly."
        ),
        aspect_ratio="auto",
        input_references=[openrouter_input_reference(src)],
        resolution=resolution,
    )
    if result.url:
        return result.url
    raise ExternalApiError("OpenRouter", "upscale returned no URL")


async def generate_openrouter_photo(
    settings: Settings,
    *,
    model: str,
    user_prompt: str,
    aspect_ratio: str = "1:1",
    reference_data_url: str | None = None,
    fallback_models: tuple[str, ...] = (),
    timeout_sec: float = DEFAULT_OPENROUTER_IMAGES_TIMEOUT_SEC,
) -> GeminiImageResult:
    """Images API с модель-специфичной обработкой референса (image_url / GPT face-desc)."""
    api_prompt, input_refs = await resolve_openrouter_photo_prompt_and_refs(
        settings,
        model=model,
        user_prompt=user_prompt,
        reference_data_url=reference_data_url,
    )

    last_exc: ExternalApiError | None = None
    candidates = ((model or "").strip(), *fallback_models)
    for idx, slug in enumerate(candidates):
        if not slug:
            continue
        try:
            prompt, refs = api_prompt, input_refs
            if idx > 0:
                prompt, refs = await resolve_openrouter_photo_prompt_and_refs(
                    settings,
                    model=slug,
                    user_prompt=user_prompt,
                    reference_data_url=reference_data_url,
                )
            return await generate_openrouter_image(
                settings,
                model=slug,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                input_references=refs,
                timeout_sec=timeout_sec,
            )
        except ExternalApiError as exc:
            last_exc = exc
            if idx + 1 < len(candidates):
                logger.warning(
                    "openrouter photo model %s failed (%s), trying %s",
                    slug,
                    exc,
                    candidates[idx + 1],
                )
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise ExternalApiError("OpenRouter", "no model candidates")
