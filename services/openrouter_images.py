"""OpenRouter Images API — общий T2I/I2I клиент для NeuroMule (умный роутинг Chatcom-style)."""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import httpx

from io import BytesIO

from config import Settings
from services.api_resilience import ExternalApiError, clip_error_text
from services.gemini_image_client import GeminiImageResult

logger = logging.getLogger(__name__)

OPENROUTER_IMAGES_URL = "https://openrouter.ai/api/v1/images"
OPENROUTER_FLUX_PAID_MODEL = "black-forest-labs/flux.2-pro"
OPENROUTER_FLUX_SCHNELL_OR_MODEL = "black-forest-labs/flux-schnell"
OPENROUTER_FLUX_DEV_OR_MODEL = "black-forest-labs/flux-1.1-pro"
OPENROUTER_NANO_BANANA2_MODEL = "google/gemini-3.1-flash-image-preview"
OPENROUTER_NANO_BANANA_PRO_MODEL = "google/gemini-3-pro-image"
OPENROUTER_GPT_IMAGE2_MODEL = "openai/gpt-image-2"
OPENROUTER_FLUX_SCHNELL_MODEL = OPENROUTER_FLUX_PAID_MODEL

# Внутренние fallback-цепочки OpenRouter Images (без сторонних провайдеров).
OPENROUTER_FLUX_STACK_FALLBACKS: tuple[str, ...] = (
    OPENROUTER_FLUX_SCHNELL_OR_MODEL,
    OPENROUTER_FLUX_DEV_OR_MODEL,
)
NANO_BANANO2_FALLBACKS: tuple[str, ...] = (
    "google/gemini-3.1-flash-image",
    OPENROUTER_FLUX_PAID_MODEL,
    *OPENROUTER_FLUX_STACK_FALLBACKS,
)
NANO_BANANO_PRO_FALLBACKS: tuple[str, ...] = (
    "google/gemini-3-pro-image-preview",
    OPENROUTER_FLUX_PAID_MODEL,
    *OPENROUTER_FLUX_STACK_FALLBACKS,
)
GPT_IMAGE2_FALLBACKS: tuple[str, ...] = (
    OPENROUTER_FLUX_PAID_MODEL,
    *OPENROUTER_FLUX_STACK_FALLBACKS,
)
DEFAULT_OPENROUTER_IMAGES_TIMEOUT_SEC = 180.0
DEFAULT_PHOTO_USER_INTENT = "professional portrait"

# Google Nano / Gemini Images — identity i2i (команды только в тексте prompt).
GOOGLE_SELFIE_I2I_PROMPT_TEMPLATE = (
    "Using the attached image strictly as a character identity reference only, "
    "generate a cinematic raw photo of this exact young woman, {user_intent}. "
    "Completely ignore the background, clothing, pose, and lighting from the reference — "
    "extract only facial identity and bone structure. "
    "Shot on 35mm film, natural skin texture, realistic facial features, "
    "cinematic lighting, highly detailed."
)

# OpenAI gpt-image-2 — inpaint-логика (лицо из image layer → новая сцена).
OPENAI_INPAINT_I2I_PROMPT_TEMPLATE = (
    "Inpaint and seamlessly integrate the face from the provided image layer "
    "into a completely new scene. A beautiful young woman, {user_intent}. "
    "Do not copy the reference background, outfit, or composition — face identity only. "
    "Maintain exact facial morphology, proportions, and features. "
    "Photorealistic, crisp details, raw texture."
)

FLUX_SELFIE_I2I_PROMPT_TEMPLATE = (
    "Using the attached image as a face identity reference only, generate a new cinematic photo "
    "of this exact young woman, {user_intent}. Preserve facial identity exactly; "
    "ignore reference background, clothing, and pose — new scene only.{film_suffix}"
)

FLUX_OPENAI_FILM_SUFFIX = (
    ", raw photo, shot on 35mm film, natural skin texture with micro-imperfections, "
    "authentic candid photography, cinematic lighting, subtle bokeh"
)
FLUX_OPENAI_NEGATIVE_IN_PROMPT = (
    "ugly, deformed, tired face, dark circles under eyes, puffy face, "
    "bad skin, old, expressionless, distorted lips"
)

NANO_BANANO_NEGATIVE_PROMPT = (
    "CGI, 3d render, airbrushed, plastic skin, smooth face, digital art, illustration"
)
NANO_CHARACTER_IDENTITY_WEIGHT = 1.0

REFERENCE_QUALITY_SUFFIX = FLUX_OPENAI_FILM_SUFFIX
IDENTITY_NEGATIVE_PROMPT = FLUX_OPENAI_NEGATIVE_IN_PROMPT

SELFIE_WOMAN_PROMPT_PREFIX = "A professional photo of a beautiful young woman, "

FACE_DESCRIBE_VISION_MODEL = "google/gemini-2.5-flash"
FACE_DESCRIBE_SYSTEM_PROMPT = (
    "Ты обязан определить пол человека на фото и начать описание строго со слов "
    "'A photo of a young woman...' или 'A female portrait...'. "
    "Далее опиши только анатомические черты лица, полностью игнорируя фон, эмоции и одежду."
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
GOOGLE_IMAGE_FALLBACK_MODELS: frozenset[str] = frozenset(
    {
        "google/gemini-3.1-flash-image",
        "google/gemini-3-pro-image-preview",
    }
)
OPENAI_FLUX_OR_MODELS: frozenset[str] = frozenset(
    {
        OPENROUTER_FLUX_PAID_MODEL,
        OPENROUTER_GPT_IMAGE2_MODEL,
    }
)

MODELS_WITH_IMAGE_REFERENCE = NANO_BANANO_OR_MODELS | OPENAI_FLUX_OR_MODELS | GOOGLE_IMAGE_FALLBACK_MODELS
MODELS_WITH_WEIGHTED_IDENTITY_REFERENCE = MODELS_WITH_IMAGE_REFERENCE
IDENTITY_REFERENCE_WEIGHT = NANO_CHARACTER_IDENTITY_WEIGHT

_NANO_FACE_PROMPT_SUFFIX = (
    ". Preserve the subject's facial identity from the face reference exactly."
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
    mid = (model or "").strip().lower()
    if mid in {m.lower() for m in NANO_BANANO_OR_MODELS}:
        return True
    return any(token in mid for token in ("nano", "banano", "banana"))


def is_google_image_face_stack(model: str) -> bool:
    """Nano Banano + Gemini Images fallback — i2i через prompt + PNG base64 ref."""
    mid = (model or "").strip().lower()
    if is_nano_banano_stack(mid):
        return True
    if mid in {m.lower() for m in GOOGLE_IMAGE_FALLBACK_MODELS}:
        return True
    return "gemini" in mid and "image" in mid


def is_openai_flux_stack(model: str) -> bool:
    mid = (model or "").strip().lower()
    if mid in {m.lower() for m in OPENAI_FLUX_OR_MODELS}:
        return True
    return any(token in mid for token in ("flux", "gpt-image", "openai"))


def build_selfie_i2i_prompt_for_model(model: str, user_intent_en: str) -> str:
    """Склейка английского intent + stack-specific шаблон (без non-JSON полей OR)."""
    intent = (user_intent_en or DEFAULT_PHOTO_USER_INTENT).strip()
    model_id = (model or "").strip()

    if model_id == OPENROUTER_GPT_IMAGE2_MODEL:
        return OPENAI_INPAINT_I2I_PROMPT_TEMPLATE.format(user_intent=intent)

    if is_google_image_face_stack(model_id):
        prompt = GOOGLE_SELFIE_I2I_PROMPT_TEMPLATE.format(user_intent=intent)
        return append_negative_prompt_directive(
            prompt,
            negative=(
                f"{NANO_BANANO_NEGATIVE_PROMPT}, copied background, duplicate scene, "
                "same outfit as reference"
            ),
        )

    if is_openai_flux_stack(model_id):
        base = FLUX_SELFIE_I2I_PROMPT_TEMPLATE.format(
            user_intent=intent,
            film_suffix=FLUX_OPENAI_FILM_SUFFIX,
        )
        return append_negative_prompt_directive(base, negative=FLUX_OPENAI_NEGATIVE_IN_PROMPT)

    return format_identity_photo_prompt(intent)


async def translate_photo_user_intent(settings: Settings, user_prompt: str) -> str:
    """RU → EN перед склейкой с английскими маркерами identity/inpaint."""
    from services.billing.translator import translate_prompt_to_english

    raw = (user_prompt or "").strip() or DEFAULT_PHOTO_USER_INTENT
    return await translate_prompt_to_english(settings, raw)


def _image_bytes_to_png_data_url(image_bytes: bytes) -> str:
    from PIL import Image

    raw = bytes(image_bytes)
    if not raw:
        raise ExternalApiError("OpenRouter", "empty reference image bytes")

    with Image.open(BytesIO(raw)) as img:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        out = BytesIO()
        img.save(out, format="PNG", optimize=True)
        png_bytes = out.getvalue()

    encoded = base64.standard_b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"


async def reference_url_to_png_data_url(reference_url: str) -> str:
    """Telegram/https/data → ``data:image/png;base64,...`` для Google identity i2i."""
    ref = (reference_url or "").strip()
    if not ref:
        raise ExternalApiError("OpenRouter", "empty reference for PNG data URL")

    if ref.startswith("data:"):
        header = ref.split(",", 1)[0].lower()
        if "image/png" in header:
            return ref
        try:
            raw = base64.b64decode(ref.split(",", 1)[1], validate=False)
        except Exception as exc:
            raise ExternalApiError("OpenRouter", "invalid reference data URL") from exc
        return _image_bytes_to_png_data_url(raw)

    if not ref.startswith(("http://", "https://")):
        raise ExternalApiError("OpenRouter", "reference must be http(s) or data URL")

    from services.streaming_download import stream_download_to_bytes

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        data = await stream_download_to_bytes(client, ref, source="openrouter_ref_png")
    if not data:
        raise ExternalApiError("OpenRouter", "failed to download reference for PNG")
    logger.info("reference encoded to PNG data URL bytes=%s", len(data))
    return _image_bytes_to_png_data_url(data)


async def ensure_png_reference_data_url(
    reference_url: str,
    *,
    reference_input_url: str | None = None,
) -> str:
    """Предпочитает уже закодированный ref; иначе кодирует в PNG data-URL."""
    cached = (reference_input_url or "").strip()
    if cached.startswith("data:image/png;base64,"):
        return cached
    raw = cached or (reference_url or "").strip()
    if not raw:
        raise ExternalApiError("OpenRouter", "empty reference for PNG encoding")
    return await reference_url_to_png_data_url(raw)


def prepend_selfie_woman_prompt(user_prompt: str) -> str:
    """При i2i-селфи — явный женский пол в начале промпта."""
    base = (user_prompt or "").strip() or "Professional portrait photo"
    low = base.lower()
    if base.startswith(SELFIE_WOMAN_PROMPT_PREFIX):
        return base
    if any(token in low for token in ("woman", "female", "девушк", "женщин", "girl")):
        return base
    return f"{SELFIE_WOMAN_PROMPT_PREFIX}{base}"


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
    base = (user_prompt or "").strip() or "Professional portrait photo"
    directive = f" [Negative prompt: {(negative or '').strip()}]"
    if directive in base:
        return base
    return f"{base}{directive}"


def append_reference_prompt_modifiers(user_prompt: str, model: str, *, has_reference: bool = False) -> str:
    base = (user_prompt or "").strip() or "Professional portrait photo"
    if has_reference:
        base = prepend_selfie_woman_prompt(base)
    model_id = (model or "").strip()

    if is_google_image_face_stack(model_id):
        if _NANO_FACE_PROMPT_SUFFIX not in base:
            base = f"{base}{_NANO_FACE_PROMPT_SUFFIX}"
        return append_negative_prompt_directive(base, negative=NANO_BANANO_NEGATIVE_PROMPT)

    if is_openai_flux_stack(model_id):
        if FLUX_OPENAI_FILM_SUFFIX not in base:
            base = f"{base}{FLUX_OPENAI_FILM_SUFFIX}"
        return append_negative_prompt_directive(base, negative=FLUX_OPENAI_NEGATIVE_IN_PROMPT)

    return base


def append_reference_quality_modifiers(user_prompt: str, *, model: str = "") -> str:
    if model:
        return append_reference_prompt_modifiers(user_prompt, model, has_reference=True)
    return append_reference_prompt_modifiers(user_prompt, OPENROUTER_FLUX_PAID_MODEL, has_reference=True)


def openrouter_input_reference(image_url: str) -> dict[str, Any]:
    url = (image_url or "").strip()
    if not url:
        raise ExternalApiError("OpenRouter", "empty input reference URL")
    return {"type": "image_url", "image_url": {"url": url}}


def openrouter_face_reference(image_url: str) -> dict[str, Any]:
    """Alias → strict ``image_url`` (OpenRouter Images не принимает type face)."""
    return openrouter_input_reference(image_url)


def openrouter_character_reference(image_url: str) -> dict[str, Any]:
    """Backward-compatible alias → ``openrouter_face_reference``."""
    return openrouter_face_reference(image_url)


def openrouter_identity_reference(image_url: str, **_: Any) -> dict[str, Any]:
    return openrouter_input_reference(image_url)


def model_uses_image_reference(model: str) -> bool:
    return (model or "").strip() in MODELS_WITH_IMAGE_REFERENCE or is_google_image_face_stack(model)


def model_uses_weighted_identity_reference(model: str) -> bool:
    return model_uses_image_reference(model)


def model_uses_face_description_prompt(model: str) -> bool:
    """gpt-image-2 использует inpaint prompt + input_references, не face-desc."""
    return False


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
        data = await stream_download_to_bytes(client, ref, source="openrouter_ref_b64")
    if not data:
        raise ExternalApiError("OpenRouter", "failed to download reference for data URL")

    mime = _mime_from_reference_url(ref)
    encoded = base64.standard_b64encode(data).decode("ascii")
    logger.info("reference encoded to data URL mime=%s bytes=%s", mime, len(data))
    return f"data:{mime};base64,{encoded}"


def reference_bytes_to_png_data_url(image_bytes: bytes) -> str:
    """Raw JPEG/PNG/WebP bytes → ``data:image/png;base64,...``."""
    return _image_bytes_to_png_data_url(image_bytes)


async def resolve_reference_input_url(reference_url: str | None) -> str | None:
    """Telegram/https/data → PNG base64 data-URL для ``input_references`` OpenRouter."""
    ref = (reference_url or "").strip()
    if not ref:
        return None
    if ref.startswith("data:image/png;base64,"):
        return ref
    return await reference_url_to_png_data_url(ref)


async def describe_reference_face_for_prompt(
    settings: Settings,
    reference_image_url: str,
) -> str:
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
    reference_data_url: str | None = None,
    reference_input_url: str | None = None,
    user_intent_en: str | None = None,
) -> tuple[str, list[dict[str, Any]] | None, dict[str, Any]]:
    """
    Промпт, input_references (PNG base64 data-URL) и body extensions по стеку модели.
    ``reference_input_url`` — уже закодированный ref (для fallback без повторной загрузки).
    """
    cleaned = (user_prompt or "").strip() or DEFAULT_PHOTO_USER_INTENT
    raw_ref = (reference_input_url or reference_data_url or "").strip()
    if not raw_ref:
        return cleaned, None, {}

    model_id = (model or "").strip()
    intent_en = (user_intent_en or "").strip()
    if not intent_en:
        intent_en = await translate_photo_user_intent(settings, cleaned)

    ref_png = await ensure_png_reference_data_url(
        reference_data_url or raw_ref,
        reference_input_url=reference_input_url,
    )
    prompt = build_selfie_i2i_prompt_for_model(model_id, intent_en)
    # OpenRouter Images API: только type image_url; identity/inpaint → в prompt.
    return prompt, [openrouter_input_reference(ref_png)], {}


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

    ref_b64 = await reference_url_to_data_url(src)
    resolution = "2K" if scale == 2 else "4K"
    result = await generate_openrouter_image(
        settings,
        model=OPENROUTER_FLUX_PAID_MODEL,
        prompt=(
            "Enhance resolution, sharpness, and fine detail. "
            "Preserve composition, subjects, colors, and facial identity exactly."
        ),
        aspect_ratio="auto",
        input_references=[openrouter_input_reference(ref_b64)],
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
    ref_png: str | None = None
    user_intent_en: str | None = None
    if (reference_data_url or "").strip():
        ref_raw = reference_data_url.strip()
        if ref_raw.startswith("data:image/png;base64,"):
            ref_png = ref_raw
        else:
            ref_png = await resolve_reference_input_url(ref_raw)
        user_intent_en = await translate_photo_user_intent(settings, user_prompt)

    last_exc: ExternalApiError | None = None
    candidates = ((model or "").strip(), *fallback_models)
    for idx, slug in enumerate(candidates):
        if not slug:
            continue
        try:
            prompt, refs, extensions = await resolve_openrouter_photo_prompt_and_refs(
                settings,
                model=slug,
                user_prompt=user_prompt,
                reference_data_url=reference_data_url,
                reference_input_url=ref_png,
                user_intent_en=user_intent_en,
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
                    "openrouter photo model %s failed (%s), trying %s (ref preserved=%s)",
                    slug,
                    exc,
                    candidates[idx + 1],
                    bool(ref_png),
                )
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise ExternalApiError("OpenRouter", "no model candidates")
