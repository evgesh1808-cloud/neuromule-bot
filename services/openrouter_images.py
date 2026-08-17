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

# Явный close-up в intent — только тогда сужаем кадр.
_TIGHT_FRAMING_INTENT_KEYWORDS: tuple[str, ...] = (
    "headshot",
    "close-up",
    "close up",
    "bust shot",
    "shoulders only",
    "face only",
    "tight portrait",
    "close portrait",
    "macro portrait",
)

# Явный полный рост / широкий кадр в intent.
_WIDER_FRAMING_INTENT_KEYWORDS: tuple[str, ...] = (
    "full body",
    "full length",
    "full-length",
    "head to toe",
    "head-to-toe",
    "full figure",
    "standing shot",
    "from knees",
)

# Только если пользователь сам просит обрезать волосы / убрать headroom.
_ALLOW_HAIR_CROP_INTENT_KEYWORDS: tuple[str, ...] = (
    "crop hair",
    "cropped hair",
    "hair cropped",
    "cut off hair",
    "hair cut off",
    "hair outside frame",
    "no headroom",
    "forehead only",
    "extreme face close-up",
    "extreme close-up face",
    "face fills entire frame",
    "top of head cropped",
    "partial head crop",
)

# Единый i2i-шаблон (Nano Banano / GPT inpaint / Flux fallback) — Chatcom-style.
SELFIE_I2I_PROMPT_TEMPLATE = (
    "{framing_directive} "
    "{hair_directive} "
    "Using the attached image STRICTLY as character identity reference only. "
    "CRITICAL: Completely override the camera distance, framing, body pose, and original crop "
    "of the reference image. Never reuse the reference waist-up cut-off, belly crop, or medium shot. "
    "Extract facial identity only — ignore reference body boundaries and aspect framing. "
    "Maintain the exact same facial identity as the reference: identical eye shape, nose bridge, "
    "jawline, lip proportions, skin tone, and apparent age. "
    "Preserve the exact hairstyle, hair length, and hair color from the reference. "
    "Do not age, rejuvenate, or add freckles/spots not in the reference. "
    "Generate this exact person in a new scene: {user_intent}. "
    "High-end editorial photography, soft beauty lighting, healthy rested appearance, "
    "clean luminous skin, sharp focus, balanced colors, 85mm lens. "
    "Ignore reference background, clothing, and pose entirely — "
    "create a completely new scene and composition."
)

# По умолчанию — шире референсного селфи (больше тела), не копировать кроп «до пояса».
SELFIE_I2I_DEFAULT_FRAMING_DIRECTIVE = (
    "OUTPUT FRAMING (mandatory): pull the camera back wider than the reference selfie. "
    "Three-quarter body or full-length editorial portrait — show substantially more body than "
    "the reference crop (head, torso, hips, and legs when the scene allows). "
    "Never copy the reference waist-up cut-off, belly boundary, or medium-shot crop."
)

SELFIE_I2I_TIGHT_FRAMING_DIRECTIVE = (
    "OUTPUT FRAMING: close-up headshot or bust portrait as described in the scene — "
    "never copy the reference image crop boundaries."
)

SELFIE_I2I_CUSTOM_FRAMING_DIRECTIVE = (
    "OUTPUT FRAMING: follow the scene description below for body framing — "
    "frame wider than the reference selfie unless the scene explicitly asks for a tight crop. "
    "Never copy the reference crop line."
)

SELFIE_I2I_HAIR_PROTECT_DIRECTIVE = (
    "HAIR FRAMING (mandatory): keep the entire head and complete hairstyle fully visible with "
    "generous headroom — never crop hair or cut off the top of the head unless the scene "
    "explicitly requests hair cropping."
)

SELFIE_I2I_HAIR_CROP_ALLOWED_DIRECTIVE = (
    "HAIR FRAMING: follow the scene description for head and hair cropping."
)

SELFIE_I2I_NEGATIVE_PROMPT = (
    "aging, wrinkles, freckles, age spots, blemishes, hyperpigmentation, "
    "detailed skin pores, skin imperfections, raw photo, 35mm film, red face, "
    "tired eyes, sunburn, alcoholic flush, dark circles, tired face, haggard, "
    "plastic skin, deformed, duplicate background, copying reference clothing, "
    "copied reference framing, same crop as reference, matching reference composition, "
    "reference waist-up crop, reference belly crop, "
    "CGI, 3d render, digital art, illustration"
)

SELFIE_I2I_HAIR_CROP_NEGATIVE = (
    "tight head crop, cropped hair, cut-off hair, hair cropped at top, head cut off, "
    "cropped head, missing hair, chopped hair"
)

OPENAI_INPAINT_I2I_PREFIX = (
    "Inpaint and seamlessly integrate the face and identity from the provided image layer "
    "into a completely new scene with wider camera framing than the reference. "
    "Do not copy reference body crop or cut-off line. "
)

# Dual-reference composite refine (base result + object/graphic upload).
COMPOSITE_REFINE_PROMPT_TEMPLATE = (
    "CRITICAL COMPOSITE EDITING DIRECTIVE:\n"
    "You are provided with two images in the input_references array.\n\n"
    "Image 1 (Base Context & Anchor): This is the main base image. Use this image strictly "
    "as the absolute anchor for the main character's facial identity, facial geometry, "
    "precise body pose, framing, and background environment. DO NOT alter, age, or change "
    "the person's face or the background from Image 1.\n\n"
    "Image 2 (Object & Graphic Reference Only): Use this image STRICTLY as a visual graphic, "
    "photo print, logo, or mirror reflection element — NOT as a second living person standing "
    "in the scene. Image 2 may be a photograph (including a younger/child version of the same "
    "person) to print on clothing, or to show as a realistic mirror/glass reflection. "
    "Do not blend, morph, or inject the facial features, age, or identity of Image 2 into "
    "the main character's face in Image 1.\n\n"
    "User Modification Request: {user_intent}"
)

# Длинный промпт + 2 фото: Image 1 = identity, Image 2 = принт, сцена из текста.
COMPOSITE_CREATIVE_SCENE_PROMPT_TEMPLATE = (
    "CRITICAL DUAL-REFERENCE CREATIVE COMPOSITE:\n"
    "You are provided with two images in input_references.\n\n"
    "Image 1 (Adult Identity Reference ONLY): Extract the woman's facial identity, age, "
    "skin tone, bone structure, and distinctive features. Generate a COMPLETELY NEW scene, "
    "pose, camera angle, lighting, wardrobe details, and background exactly as described "
    "below. DO NOT copy Image 1 background, pose, crop, or clothing.\n\n"
    "Image 2 (Childhood / Print Photo Reference ONLY): Use strictly as a vintage faded "
    "photographic print on the t-shirt chest (or placement requested). Preserve the child's "
    "face, age, and likeness from Image 2 inside the fabric print only. Never render Image 2 "
    "as a second living person in the scene.\n\n"
    "Scene and styling request: {user_intent}"
)

COMPOSITE_REFINE_NEGATIVE_PROMPT = (
    "changing main facial identity, blending image 2 features into the face, "
    "altering background of image 1, shifting body pose, changing character proportions, "
    "deforming the graphic print, second person standing next to subject, age morphing"
)

COMPOSITE_CREATIVE_NEGATIVE_PROMPT = (
    "changing adult facial identity from image 1, blending image 2 child features into adult face, "
    "second living person in scene, age morphing the adult subject, deforming fabric print, "
    "illustration, CGI, cartoon, plastic skin"
)

COMPOSITE_INTENT_COMPRESS_THRESHOLD = 1400
COMPOSITE_INTENT_MAX_CHARS = 1200

_COMPOSITE_CREATIVE_SCENE_MARKERS: tuple[str, ...] = (
    "селфи",
    "selfie",
    "интерьер",
    "interior",
    "освещ",
    "lighting",
    "ракурс",
    "pose",
    "сидит",
    "стоит",
    "комнат",
    "background",
    "тиара",
    "tiara",
    "макияж",
    "makeup",
    "photorealistic",
    "8k",
    "8к",
    "portrait",
    "портрет",
    "фотореал",
)

COMPOSITE_MIRROR_PLACEMENT_SUFFIX = (
    "\n\nMIRROR PLACEMENT (mandatory when requested): Image 2 must appear ONLY as a realistic "
    "mirror or glass reflection, preserving Image 2 subject age and appearance exactly. "
    "The reflection must not replace or alter the main person in Image 1."
)

COMPOSITE_PHOTO_PRINT_PLACEMENT_SUFFIX = (
    "\n\nPHOTO PRINT PLACEMENT (mandatory when requested): Image 2 must appear as a flat "
    "photographic print on fabric (natural folds and perspective) — including a younger/child "
    "photo of the same person — not as a second living person in the scene."
)

COMPOSITE_REFINE_FALLBACKS: tuple[str, ...] = (
    OPENROUTER_GPT_IMAGE2_MODEL,
    OPENROUTER_NANO_BANANA2_MODEL,
    "google/gemini-3-pro-image-preview",
)

CREATIVE_COMPOSITE_FALLBACKS: tuple[str, ...] = (
    OPENROUTER_GPT_IMAGE2_MODEL,
    OPENROUTER_NANO_BANANA_PRO_MODEL,
    OPENROUTER_NANO_BANANA2_MODEL,
    "google/gemini-3-pro-image-preview",
)

COMPOSITE_API_REF_MAX_SIDE_PX = 1280
COMPOSITE_PROMPT_MAX_CHARS = 5500

MULTI_REF_GROUP_NEGATIVE_PROMPT = (
    "blended faces, merged identities, averaged facial features, duplicate faces, "
    "face morphing, unrecognizable subjects, deformed group portrait"
)

MULTI_REF_GROUP_FALLBACKS: tuple[str, ...] = NANO_BANANO_PRO_FALLBACKS

GOOGLE_IDENTITY_LOCK = (
    "Maintain the exact same facial identity as the reference: identical eye shape, "
    "nose bridge, jawline, lip proportions, skin tone, and apparent age. "
    "Do not age, rejuvenate, or add freckles/spots not in the reference."
)
GOOGLE_SELFIE_I2I_PROMPT_TEMPLATE = SELFIE_I2I_PROMPT_TEMPLATE
OPENAI_INPAINT_I2I_PROMPT_TEMPLATE = (
    OPENAI_INPAINT_I2I_PREFIX + SELFIE_I2I_PROMPT_TEMPLATE
)
FLUX_SELFIE_I2I_PROMPT_TEMPLATE = SELFIE_I2I_PROMPT_TEMPLATE

# T2I без референса — чистая сцена без identity-lock.
PLAIN_T2I_QUALITY_SUFFIX = (
    ", high-end editorial photography, soft natural lighting, photorealistic, "
    "sharp focus, balanced colors, 85mm lens"
)

FLUX_EDITORIAL_SUFFIX = (
    ", high-end editorial portrait photography, soft diffused beauty lighting, "
    "healthy rested appearance, clean natural skin, 85mm lens, shallow depth of field"
)

# Убраны «micro-imperfections» и «raw film» — они давали веснушки и «усталое» лицо.
FLUX_OPENAI_FILM_SUFFIX = FLUX_EDITORIAL_SUFFIX

IDENTITY_NEGATIVE_IN_PROMPT = SELFIE_I2I_NEGATIVE_PROMPT
NANO_BANANO_NEGATIVE_PROMPT = SELFIE_I2I_NEGATIVE_PROMPT
FLUX_OPENAI_NEGATIVE_IN_PROMPT = SELFIE_I2I_NEGATIVE_PROMPT
NANO_CHARACTER_IDENTITY_WEIGHT = 1.0

REFERENCE_QUALITY_SUFFIX = FLUX_OPENAI_FILM_SUFFIX
IDENTITY_NEGATIVE_PROMPT = IDENTITY_NEGATIVE_IN_PROMPT

SELFIE_WOMAN_PROMPT_PREFIX = "A professional photo of a beautiful young woman, "

FACE_DESCRIBE_VISION_MODEL = "google/gemini-2.5-flash"
FACE_DESCRIBE_SYSTEM_PROMPT = (
    "Ты обязан определить пол человека на фото и начать описание строго со слов "
    "'A photo of a young woman...' или 'A female portrait...'. "
    "Далее опиши только анатомические черты лица, полностью игнорируя фон, эмоции и одежду."
)

OPENROUTER_MODEL_BY_MENU_KEY: dict[str, str] = {
    "flux_2_pro": OPENROUTER_FLUX_PAID_MODEL,
    "gpt_image_2": OPENROUTER_GPT_IMAGE2_MODEL,
    "nano_banana_2": OPENROUTER_NANO_BANANA2_MODEL,
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
    from services.billing.image_pipeline import normalize_image_model

    normalized = normalize_image_model(model_key)
    slug = OPENROUTER_MODEL_BY_MENU_KEY.get(normalized)
    if not slug:
        raise ExternalApiError("OpenRouter", f"unknown image model key: {model_key}")
    return slug


def _dedupe_model_slugs(*chains: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for chain in chains:
        for slug in chain:
            s = (slug or "").strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return tuple(out)


def resolve_composite_refine_model_key(model_key: str) -> str:
    """Dual-reference composite → OpenRouter stack выбранной модели меню."""
    from services.billing.image_pipeline import normalize_image_model

    normalized = normalize_image_model(model_key)
    if normalized in OPENROUTER_MODEL_BY_MENU_KEY:
        return resolve_openrouter_model_for_menu_key(normalized)
    return OPENROUTER_NANO_BANANA_PRO_MODEL


def resolve_composite_refine_fallbacks(model_key: str) -> tuple[str, ...]:
    """Fallback-цепочка composite после primary slug (без дубликата primary)."""
    from services.billing.image_pipeline import normalize_image_model

    normalized = normalize_image_model(model_key)
    primary = resolve_composite_refine_model_key(normalized)
    if normalized == "flux_2_pro":
        chain = _dedupe_model_slugs(
            OPENROUTER_FLUX_STACK_FALLBACKS,
            GPT_IMAGE2_FALLBACKS,
            (OPENROUTER_NANO_BANANA_PRO_MODEL, OPENROUTER_NANO_BANANA2_MODEL),
            COMPOSITE_REFINE_FALLBACKS,
        )
    elif normalized == "gpt_image_2":
        chain = _dedupe_model_slugs(
            GPT_IMAGE2_FALLBACKS,
            (OPENROUTER_NANO_BANANA_PRO_MODEL, OPENROUTER_NANO_BANANA2_MODEL),
            COMPOSITE_REFINE_FALLBACKS,
        )
    elif normalized == "nano_banana_2":
        chain = _dedupe_model_slugs(
            NANO_BANANO2_FALLBACKS,
            (OPENROUTER_GPT_IMAGE2_MODEL, OPENROUTER_NANO_BANANA_PRO_MODEL),
            COMPOSITE_REFINE_FALLBACKS,
        )
    elif normalized == "nano_banana_pro":
        chain = _dedupe_model_slugs(
            NANO_BANANO_PRO_FALLBACKS,
            (OPENROUTER_GPT_IMAGE2_MODEL, OPENROUTER_NANO_BANANA2_MODEL),
            COMPOSITE_REFINE_FALLBACKS,
        )
    else:
        chain = COMPOSITE_REFINE_FALLBACKS
    return tuple(slug for slug in chain if slug != primary)


def resolve_creative_composite_fallbacks(model_key: str) -> tuple[str, ...]:
    """Fallback для creative composite (длинный scene + 2 фото)."""
    from services.billing.image_pipeline import normalize_image_model

    normalized = normalize_image_model(model_key)
    primary = resolve_composite_refine_model_key(normalized)
    chain = _dedupe_model_slugs(
        resolve_composite_refine_fallbacks(normalized),
        CREATIVE_COMPOSITE_FALLBACKS,
        (OPENROUTER_FLUX_PAID_MODEL, *OPENROUTER_FLUX_STACK_FALLBACKS),
    )
    return tuple(slug for slug in chain if slug != primary)


def should_use_creative_composite_template(user_intent: str) -> bool:
    """Длинный scene-промпт: Image 1 только identity, сцена из текста."""
    low = (user_intent or "").strip().lower()
    if not low:
        return False
    if len(low) > 450:
        return True
    hits = sum(1 for marker in _COMPOSITE_CREATIVE_SCENE_MARKERS if marker in low)
    return hits >= 3


async def _summarize_composite_intent_for_api(settings: Settings, user_prompt: str) -> str:
    """Сжимает очень длинный RU/EN промпт для dual-ref composite."""
    from services.ai_text import ask_ai_messages
    from services.billing.pricing import FREE_CHAT_MODEL

    text = (user_prompt or "").strip()
    if not text:
        return DEFAULT_PHOTO_USER_INTENT
    if not (settings.openrouter_key or "").strip():
        return text[:COMPOSITE_INTENT_MAX_CHARS]

    instruction = (
        "Summarize this dual-reference image generation request into concise professional "
        "English (max 900 characters) for an AI image API.\n"
        "Rules:\n"
        "- Image 1 = adult woman identity reference ONLY (preserve her face/age).\n"
        "- Image 2 = childhood photo used ONLY as a vintage faded print on the t-shirt.\n"
        "- Describe the NEW scene: pose, selfie angle, outfit, tiara, interior, warm lighting.\n"
        "- Do NOT ask to copy Image 1 background or pose.\n"
        "Output ONLY the English summary, no quotes.\n\n"
        f"{text}"
    )
    try:
        out = await ask_ai_messages(
            settings,
            [{"role": "user", "content": instruction}],
            timeout=min(float(settings.openrouter_timeout_sec or 30), 45.0),
            models=[FREE_CHAT_MODEL],
        )
        summary = (out.get("content") or "").strip()
        if summary:
            return summary[:COMPOSITE_INTENT_MAX_CHARS]
    except Exception:
        logger.warning("composite intent summarize failed, using truncated original", exc_info=True)
    return text[:COMPOSITE_INTENT_MAX_CHARS]


async def prepare_composite_user_intent(
    settings: Settings,
    user_prompt: str,
) -> tuple[str, bool]:
    """EN intent + whether to use creative composite template."""
    raw = (user_prompt or "").strip() or DEFAULT_PHOTO_USER_INTENT
    creative = should_use_creative_composite_template(raw)
    if len(raw) > COMPOSITE_INTENT_COMPRESS_THRESHOLD:
        intent_en = await _summarize_composite_intent_for_api(settings, raw)
    else:
        intent_en = await translate_photo_user_intent(settings, raw)
    if not creative:
        creative = should_use_creative_composite_template(intent_en)
    return intent_en.strip() or DEFAULT_PHOTO_USER_INTENT, creative


def build_minimal_child_print_composite_intent(user_intent_en: str) -> str:
    """Короткий fallback после сбоя длинного creative composite."""
    tail = (user_intent_en or "").strip()
    if len(tail) > 400:
        tail = f"{tail[:397]}…"
    base = (
        "Photorealistic cozy indoor selfie of the adult woman from Image 1 — preserve her "
        "exact adult facial identity, skin tone, and age. White oversize t-shirt. "
        "Place Image 2 childhood photo as a faded vintage rectangular print on the t-shirt chest "
        "(fabric folds, retro look). Warm soft indoor lighting, natural skin, no second person."
    )
    if tail:
        return f"{base} Scene notes: {tail}"
    return base


def _clip_composite_prompt(prompt: str) -> str:
    text = (prompt or "").strip()
    if len(text) <= COMPOSITE_PROMPT_MAX_CHARS:
        return text
    return f"{text[: COMPOSITE_PROMPT_MAX_CHARS - 1].rstrip()}…"


def build_composite_refine_prompt(
    user_intent: str,
    *,
    base_image_url: str,
    object_image_url: str,
    creative_scene: bool = False,
) -> dict[str, Any]:
    """OpenRouter payload fragment: prompt + ordered dual ``input_references``."""
    base_url = (base_image_url or "").strip()
    object_url = (object_image_url or "").strip()
    if not base_url or not object_url:
        raise ExternalApiError("OpenRouter", "composite refine requires two image URLs")

    intent = (user_intent or DEFAULT_PHOTO_USER_INTENT).strip()
    template = (
        COMPOSITE_CREATIVE_SCENE_PROMPT_TEMPLATE
        if creative_scene
        else COMPOSITE_REFINE_PROMPT_TEMPLATE
    )
    prompt = template.format(user_intent=intent)
    from services.photo_multi_ref_routing import (
        is_composite_print_intent,
        is_mirror_reflection_intent,
    )

    if not creative_scene and is_mirror_reflection_intent(intent):
        prompt = f"{prompt}{COMPOSITE_MIRROR_PLACEMENT_SUFFIX}"
    elif is_composite_print_intent(intent) or creative_scene:
        prompt = f"{prompt}{COMPOSITE_PHOTO_PRINT_PLACEMENT_SUFFIX}"
    negative = (
        COMPOSITE_CREATIVE_NEGATIVE_PROMPT if creative_scene else COMPOSITE_REFINE_NEGATIVE_PROMPT
    )
    prompt = append_negative_prompt_directive(
        prompt,
        negative=negative,
    )
    prompt = _clip_composite_prompt(prompt)
    return {
        "prompt": prompt,
        "input_references": [
            openrouter_input_reference(base_url),
            openrouter_input_reference(object_url),
        ],
    }


async def resolve_reference_to_png_data_url(
    *,
    bot: Any | None,
    file_id: str | None = None,
    reference_url: str | None = None,
    reference_bytes: bytes | None = None,
    reference_mime: str = "image/jpeg",
) -> str:
    """Telegram file_id / URL / bytes → PNG base64 data-URL для OpenRouter Images."""
    resolved = await resolve_openrouter_reference_url(
        bot=bot,
        file_id=file_id,
        reference_image_url=reference_url,
        reference_image_bytes=reference_bytes,
        reference_mime=reference_mime,
    )
    if not resolved:
        raise ExternalApiError("OpenRouter", "empty composite reference")
    if resolved.startswith("data:image/png;base64,"):
        return resolved
    return await ensure_png_reference_data_url(resolved)


async def generate_openrouter_composite_photo(
    settings: Settings,
    *,
    model: str,
    user_prompt: str,
    base_image_data_url: str,
    object_image_data_url: str,
    aspect_ratio: str = "1:1",
    model_key: str | None = None,
    fallback_models: tuple[str, ...] | None = None,
    timeout_sec: float = DEFAULT_OPENROUTER_IMAGES_TIMEOUT_SEC,
) -> GeminiImageResult:
    """Dual-reference composite refine via OpenRouter Images API."""
    from services.billing.image_pipeline import normalize_image_model

    menu_key = normalize_image_model(model_key or "")
    intent_en, creative_scene = await prepare_composite_user_intent(settings, user_prompt)
    base_png = resize_png_data_url_for_api(await ensure_png_reference_data_url(base_image_data_url))
    object_png = resize_png_data_url_for_api(await ensure_png_reference_data_url(object_image_data_url))
    base_fallbacks = fallback_models or resolve_composite_refine_fallbacks(menu_key)
    fallbacks = (
        resolve_creative_composite_fallbacks(menu_key)
        if creative_scene
        else base_fallbacks
    )

    async def _attempt(intent: str, *, creative: bool, models: tuple[str, ...]) -> GeminiImageResult:
        last_exc: ExternalApiError | None = None
        candidates = ((model or "").strip(), *models)
        for idx, slug in enumerate(candidates):
            if not slug:
                continue
            try:
                payload = build_composite_refine_prompt(
                    intent,
                    base_image_url=base_png,
                    object_image_url=object_png,
                    creative_scene=creative,
                )
                return await generate_openrouter_image(
                    settings,
                    model=slug,
                    prompt=str(payload["prompt"]),
                    aspect_ratio=aspect_ratio,
                    input_references=payload["input_references"],
                    timeout_sec=timeout_sec,
                )
            except ExternalApiError as exc:
                last_exc = exc
                if idx + 1 < len(candidates):
                    logger.warning(
                        "openrouter composite model %s failed (%s), trying %s",
                        slug,
                        exc,
                        candidates[idx + 1],
                    )
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise ExternalApiError("OpenRouter", "no composite model candidates")

    try:
        return await _attempt(intent_en, creative=creative_scene, models=fallbacks)
    except ExternalApiError as first_exc:
        minimal = build_minimal_child_print_composite_intent(intent_en)
        logger.warning(
            "composite full prompt failed (%s), retrying minimal child-print intent",
            first_exc,
        )
        try:
            return await _attempt(
                minimal,
                creative=True,
                models=resolve_creative_composite_fallbacks(menu_key),
            )
        except ExternalApiError:
            raise first_exc from None


def _build_multi_ref_identity_lines(num_refs: int) -> str:
    lines: list[str] = []
    for idx in range(max(1, num_refs)):
        lines.append(
            f"- Persona {idx + 1} in the final image MUST look exactly like "
            f"input_references[{idx}]. Preserve bone geometry, expressions, and traits strictly."
        )
    return "\n".join(lines)


def build_multi_banana_prompt(user_intent_en: str, num_refs: int) -> str:
    """Composite prompt for multi-reference group portrait (Nano Banana Pro)."""
    count = max(2, min(int(num_refs or 0), 10))
    intent = (user_intent_en or DEFAULT_PHOTO_USER_INTENT).strip()
    identity_block = _build_multi_ref_identity_lines(count)
    prompt = (
        "CRITICAL MULTI-SUBJECT IDENTITY DIRECTIVE:\n"
        f"You are provided with exactly {count} individual face images in the input_references array.\n"
        f"{identity_block}\n"
        "STRICTLY FORBIDDEN to blend, merge, or average facial features between references. "
        "Each character must remain completely distinct and 100% recognizable. "
        "Deep crisp focus on ALL faces.\n\n"
        f"Scene and placement request: {intent}"
    )
    return append_negative_prompt_directive(
        prompt,
        negative=MULTI_REF_GROUP_NEGATIVE_PROMPT,
    )


async def build_multi_banana_prompt_from_ru(
    settings: Settings,
    user_prompt_ru: str,
    num_refs: int,
) -> str:
    intent_en = await translate_photo_user_intent(settings, user_prompt_ru)
    return build_multi_banana_prompt(intent_en, num_refs)


def build_multi_ref_group_payload(
    user_intent_en: str,
    reference_image_urls: list[str],
) -> dict[str, Any]:
    urls = [(url or "").strip() for url in reference_image_urls if (url or "").strip()]
    if len(urls) < 2:
        raise ExternalApiError("OpenRouter", "multi-ref group requires at least 2 references")
    if len(urls) > 10:
        urls = urls[:10]
    prompt = build_multi_banana_prompt(user_intent_en, len(urls))
    return {
        "prompt": prompt,
        "input_references": [openrouter_input_reference(url) for url in urls],
    }


async def generate_openrouter_multi_ref_group_photo(
    settings: Settings,
    *,
    model: str,
    user_prompt: str,
    reference_image_data_urls: list[str],
    aspect_ratio: str = "1:1",
    fallback_models: tuple[str, ...] = MULTI_REF_GROUP_FALLBACKS,
    timeout_sec: float = DEFAULT_OPENROUTER_IMAGES_TIMEOUT_SEC,
) -> GeminiImageResult:
    """Multi-reference group portrait via OpenRouter Images API."""
    intent_en = await translate_photo_user_intent(settings, user_prompt)
    png_refs: list[str] = []
    for raw_url in reference_image_data_urls:
        png_refs.append(await ensure_png_reference_data_url(raw_url))

    last_exc: ExternalApiError | None = None
    candidates = ((model or "").strip(), *fallback_models)
    for idx, slug in enumerate(candidates):
        if not slug:
            continue
        try:
            payload = build_multi_ref_group_payload(intent_en, png_refs)
            return await generate_openrouter_image(
                settings,
                model=slug,
                prompt=str(payload["prompt"]),
                aspect_ratio=aspect_ratio,
                input_references=payload["input_references"],
                timeout_sec=timeout_sec,
            )
        except ExternalApiError as exc:
            last_exc = exc
            if idx + 1 < len(candidates):
                logger.warning(
                    "openrouter multi-ref group model %s failed (%s), trying %s",
                    slug,
                    exc,
                    candidates[idx + 1],
                )
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise ExternalApiError("OpenRouter", "no multi-ref group model candidates")


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


def user_allows_hair_crop(user_intent_en: str) -> bool:
    """Обрезка волос допустима только если пользователь явно просит в intent."""
    low = (user_intent_en or "").lower()
    return any(keyword in low for keyword in _ALLOW_HAIR_CROP_INTENT_KEYWORDS)


def resolve_selfie_hair_directive(user_intent_en: str) -> str:
    if user_allows_hair_crop(user_intent_en):
        return SELFIE_I2I_HAIR_CROP_ALLOWED_DIRECTIVE
    return SELFIE_I2I_HAIR_PROTECT_DIRECTIVE


def build_selfie_i2i_negative_prompt(user_intent_en: str) -> str:
    negative = SELFIE_I2I_NEGATIVE_PROMPT
    if not user_allows_hair_crop(user_intent_en):
        negative = f"{negative}, {SELFIE_I2I_HAIR_CROP_NEGATIVE}"
    return negative


def resolve_selfie_framing_directive(user_intent_en: str) -> str:
    """По умолчанию — шире референса; close-up только если пользователь явно просит."""
    low = (user_intent_en or "").lower()
    if any(keyword in low for keyword in _TIGHT_FRAMING_INTENT_KEYWORDS):
        return SELFIE_I2I_TIGHT_FRAMING_DIRECTIVE
    if any(keyword in low for keyword in _WIDER_FRAMING_INTENT_KEYWORDS):
        return SELFIE_I2I_CUSTOM_FRAMING_DIRECTIVE
    return SELFIE_I2I_DEFAULT_FRAMING_DIRECTIVE


def build_selfie_i2i_prompt_for_model(model: str, user_intent_en: str) -> str:
    """Склейка английского intent + единый i2i-шаблон (без non-JSON полей OR)."""
    intent = (user_intent_en or DEFAULT_PHOTO_USER_INTENT).strip()
    model_id = (model or "").strip()
    framing = resolve_selfie_framing_directive(intent)
    hair = resolve_selfie_hair_directive(intent)
    negative = build_selfie_i2i_negative_prompt(intent)

    if model_id == OPENROUTER_GPT_IMAGE2_MODEL:
        base = OPENAI_INPAINT_I2I_PREFIX + SELFIE_I2I_PROMPT_TEMPLATE.format(
            framing_directive=framing,
            hair_directive=hair,
            user_intent=intent,
        )
    else:
        base = SELFIE_I2I_PROMPT_TEMPLATE.format(
            framing_directive=framing,
            hair_directive=hair,
            user_intent=intent,
        )

    return append_negative_prompt_directive(base, negative=negative)


def build_plain_t2i_prompt(user_intent_en: str) -> str:
    """T2I без референса: сцена + editorial quality (без identity-lock)."""
    intent = (user_intent_en or DEFAULT_PHOTO_USER_INTENT).strip()
    if PLAIN_T2I_QUALITY_SUFFIX.strip(", ") in intent:
        return intent
    return f"{intent}{PLAIN_T2I_QUALITY_SUFFIX}"


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


def resize_png_data_url_for_api(data_url: str, *, max_side: int = COMPOSITE_API_REF_MAX_SIDE_PX) -> str:
    """Уменьшает PNG data-URL для OpenRouter (dual-ref payload)."""
    ref = (data_url or "").strip()
    if not ref.startswith("data:"):
        return ref
    try:
        raw = base64.b64decode(ref.split(",", 1)[1], validate=False)
    except Exception as exc:
        raise ExternalApiError("OpenRouter", "invalid reference data URL") from exc
    from PIL import Image

    with Image.open(BytesIO(raw)) as img:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        width, height = img.size
        longest = max(width, height)
        if longest > max_side:
            scale = max_side / float(longest)
            img = img.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.Resampling.LANCZOS,
            )
        out = BytesIO()
        img.save(out, format="PNG", optimize=True)
        return _image_bytes_to_png_data_url(out.getvalue())


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
        intent_en = (user_intent_en or "").strip()
        if not intent_en:
            intent_en = await translate_photo_user_intent(settings, cleaned)
        return build_plain_t2i_prompt(intent_en), None, {}

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
    if not src.startswith(("http://", "https://", "data:")):
        raise ExternalApiError("OpenRouter", "upscale requires http(s) or data image URL")

    scale = int(scale_value)
    if scale not in (2, 4):
        raise ExternalApiError("OpenRouter", f"unsupported scale_value={scale}")

    if src.startswith("data:"):
        ref_b64 = src
    else:
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
