"""OpenRouter Images API — общий T2I/I2I клиент для NeuroMule (умный роутинг Chatcom-style)."""

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
REPLICATE_FLUX_SCHNELL_MODEL = "black-forest-labs/flux-schnell"
OPENROUTER_FLUX_PAID_MODEL = "black-forest-labs/flux.2-pro"
OPENROUTER_NANO_BANANA2_MODEL = "google/gemini-3.1-flash-image-preview"
OPENROUTER_NANO_BANANA_PRO_MODEL = "google/gemini-3-pro-image"
OPENROUTER_GPT_IMAGE2_MODEL = "openai/gpt-image-2"
OPENROUTER_FLUX_SCHNELL_MODEL = OPENROUTER_FLUX_PAID_MODEL
DEFAULT_OPENROUTER_IMAGES_TIMEOUT_SEC = 180.0

# Стек Flux / OpenAI — плёночный фотореализм (без глянца).
FLUX_OPENAI_FILM_SUFFIX = (
    ", raw photo, shot on 35mm film, natural skin texture with micro-imperfections, "
    "authentic candid photography, cinematic lighting, subtle bokeh"
)
FLUX_OPENAI_NEGATIVE_IN_PROMPT = (
    "ugly, deformed, tired face, dark circles under eyes, puffy face, "
    "bad skin, old, expressionless, distorted lips"
)

# Стек Google Nano Banano — anti-CGI в корневом negative_prompt (где API поддерживает).
NANO_BANANO_NEGATIVE_PROMPT = (
    "CGI, 3d render, airbrushed, plastic skin, smooth face, digital art, illustration"
)
NANO_CHARACTER_IDENTITY_WEIGHT = 1.0

# Backward-compatible aliases (старые тесты / импорты).
REFERENCE_QUALITY_SUFFIX = FLUX_OPENAI_FILM_SUFFIX
IDENTITY_NEGATIVE_PROMPT = FLUX_OPENAI_NEGATIVE_IN_PROMPT

FACE_DESCRIBE_VISION_MODEL = "google/gemini-2.5-flash"
FACE_DESCRIBE_SYSTEM_PROMPT = (
    "Опиши только неизменяемые анатомические черты лица (форма глаз, носа, губ, "
    "структура скул, прическа). Полностью игнорируй эмоции, одежду, фон, ракурс кадра "
    "и освещение. Описание должно быть ультра-лаконичным, без художественных прикрас, "
    "чтобы модель генерации не копировала композицию исходного селфи."
)

OPENROUTER_MODEL_BY_MENU_KEY: dict[str, str] = {
    "flux_schnell": OPENROUTER_FLUX_PAID_MODEL,
    "dalle_3": OPENROUTER_GPT_IMAGE2_MODEL,
    "nano_banana2": OPENROUTER_NANO_BANANA2_MODEL,
    "nano_banana_pro": OPENROUTER_NANO_BANANA_PRO_MODEL,
}

NANO_BANANO_OR_MODELS: frozenset[str] = frozenset(
    {
        OPENROUTER_NANO_BANANA2_MODEL,
        OPENROUTER_NANO_BANANA_PRO_MODEL,
    }
)
OPENAI_FLUX_OR_MODELS: frozenset[str] = frozenset(
    {
        OPENROUTER_FLUX_PAID_MODEL,
        OPENROUTER_GPT_IMAGE2_MODEL,
    }
)

MODELS_WITH_IMAGE_REFERENCE = NANO_BANANO_OR_MODELS | OPENAI_FLUX_OR_MODELS
MODELS_WITH_WEIGHTED_IDENTITY_REFERENCE = MODELS_WITH_IMAGE_REFERENCE
IDENTITY_REFERENCE_WEIGHT = NANO_CHARACTER_IDENTITY_WEIGHT

_NANO_CHARACTER_PROMPT_SUFFIX = (
    ". Preserve the subject's facial identity from the character reference exactly."
)


def openrouter_images_configured(settings: Settings) -> bool:
    return bool((settings.openrouter_key or "").strip())


def resolve_openrouter_model_for_menu_key(model_key: str) -> str:
    normalized = (model_key or "").strip().lower().replace("-", "_")
    slug = OPENROUTER_MODEL_BY_MENU_KEY.get(normalized)
    if not slug:
        raise ExternalApiError("OpenRouter", f"unknown image model key: {model_key}")
    return slug


def is_nano_banano_stack(model: str) -> bool:
    """Google Nano Banano 2 / Pro (character reference + identity body)."""
    mid = (model or "").strip().lower()
    if mid in {m.lower() for m in NANO_BANANO_OR_MODELS}:
        return True
    return any(token in mid for token in ("nano", "banano", "banana"))


def is_openai_flux_stack(model: str) -> bool:
    """Flux 2 Pro / OpenAI gpt-image (строгий image_url, без identity в корне)."""
    mid = (model or "").strip().lower()
    if mid in {m.lower() for m in OPENAI_FLUX_OR_MODELS}:
        return True
    return any(token in mid for token in ("flux", "gpt-image", "openai"))


def format_identity_photo_prompt(user_prompt: str) -> str:
    cleaned = (user_prompt or "").strip() or "Professional portrait photo"
    return (
        f"{cleaned}. Generate a new scene matching the description. "
        "Preserve the subject's facial identity, bone structure, skin tone, and "
        "distinctive features from the reference exactly — new background and styling only."
    )


def append_negative_prompt_directive(
    user_prompt: str,
    *,
    negative: str = FLUX_OPENAI_NEGATIVE_IN_PROMPT,
) -> str:
    """Негатив в текст prompt (Flux/OpenAI — API не принимает negative_prompt в JSON)."""
    base = (user_prompt or "").strip() or "Professional portrait photo"
    directive = f" [Negative prompt: {(negative or '').strip()}]"
    if directive in base:
        return base
    return f"{base}{directive}"


def append_reference_prompt_modifiers(user_prompt: str, model: str) -> str:
    """Модификаторы промпта по стеку провайдера (anti-gloss vs character)."""
    base = (user_prompt or "").strip() or "Professional portrait photo"
    model_id = (model or "").strip()

    if is_nano_banano_stack(model_id):
        if _NANO_CHARACTER_PROMPT_SUFFIX not in base:
            base = f"{base}{_NANO_CHARACTER_PROMPT_SUFFIX}"
        return base

    if is_openai_flux_stack(model_id):
        if FLUX_OPENAI_FILM_SUFFIX not in base:
            base = f"{base}{FLUX_OPENAI_FILM_SUFFIX}"
        return append_negative_prompt_directive(base, negative=FLUX_OPENAI_NEGATIVE_IN_PROMPT)

    return base


def append_reference_quality_modifiers(user_prompt: str, *, model: str = "") -> str:
    """Backward-compatible alias → ``append_reference_prompt_modifiers``."""
    if model:
        return append_reference_prompt_modifiers(user_prompt, model)
    return append_reference_prompt_modifiers(user_prompt, OPENROUTER_FLUX_PAID_MODEL)


def openrouter_input_reference(image_url: str) -> dict[str, Any]:
    url = (image_url or "").strip()
    if not url:
        raise ExternalApiError("OpenRouter", "empty input reference URL")
    return {"type": "image_url", "image_url": {"url": url}}


def openrouter_character_reference(image_url: str) -> dict[str, Any]:
    """Google Nano Banano: Character Reference для вклейки лица."""
    url = (image_url or "").strip()
    if not url:
        raise ExternalApiError("OpenRouter", "empty character reference URL")
    return {"type": "character", "image_url": {"url": url}}


def openrouter_identity_reference(image_url: str, **_: Any) -> dict[str, Any]:
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


def build_nano_banano_body_extensions() -> dict[str, Any]:
    """Корневые поля identity для стека Google (не для Flux/OpenAI)."""
    return {
        "identity": True,
        "identity_weight": NANO_CHARACTER_IDENTITY_WEIGHT,
        "negative_prompt": NANO_BANANO_NEGATIVE_PROMPT,
    }


async def resolve_openrouter_reference_url(
    *,
    bot: Any | None,
    file_id: str | None,
    reference_image_url: str | None = None,
    reference_image_bytes: bytes | None = None,
    reference_mime: str = "image/jpeg",
) -> str | None:
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
    """GPT Image 2: анатомическое описание лица через vision (data URL)."""
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
) -> tuple[str, list[dict[str, Any]] | None, dict[str, Any]]:
    """
    Промпт, input_references и доп. поля body по стеку модели.

    Nano Banano → type character + identity в корне.
    Flux / gpt-image → type image_url, негатив только в тексте prompt.
    """
    _ = settings
    cleaned = (user_prompt or "").strip() or "Professional portrait photo"
    ref = (reference_data_url or "").strip()
    if not ref:
        return cleaned, None, {}

    model_id = (model or "").strip()
    body_extensions: dict[str, Any] = {}

    if model_uses_face_description_prompt(model_id):
        cleaned = append_reference_prompt_modifiers(cleaned, model_id)
        face_desc = await describe_reference_face_for_prompt(settings, ref)
        return append_face_description_to_prompt(cleaned, face_desc), None, body_extensions

    cleaned = append_reference_prompt_modifiers(cleaned, model_id)

    if is_nano_banano_stack(model_id):
        body_extensions = build_nano_banano_body_extensions()
        return cleaned, [openrouter_character_reference(ref)], body_extensions

    if is_openai_flux_stack(model_id):
        return cleaned, [openrouter_input_reference(ref)], body_extensions

    return cleaned, None, body_extensions


def parse_openrouter_image_payload(payload: dict[str, Any]) -> GeminiImageResult:
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


_parse_openrouter_image_payload = parse_openrouter_image_payload


def _merge_body_extensions(body: dict[str, Any], extensions: dict[str, Any] | None) -> None:
    if not extensions:
        return
    for key, value in extensions.items():
        if value is not None:
            body[key] = value


async def generate_openrouter_image(
    settings: Settings,
    *,
    model: str,
    prompt: str,
    aspect_ratio: str = "1:1",
    input_references: list[dict[str, Any]] | None = None,
    body_extensions: dict[str, Any] | None = None,
    resolution: str | None = None,
    timeout_sec: float = DEFAULT_OPENROUTER_IMAGES_TIMEOUT_SEC,
) -> GeminiImageResult:
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
    _merge_body_extensions(body, body_extensions)

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
    api_prompt, input_refs, body_ext = await resolve_openrouter_photo_prompt_and_refs(
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
            prompt, refs, extensions = api_prompt, input_refs, body_ext
            if idx > 0:
                prompt, refs, extensions = await resolve_openrouter_photo_prompt_and_refs(
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
                body_extensions=extensions,
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
