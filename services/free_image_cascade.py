"""Flux FREE: Pollinations → Gemini spare → (опц.) OpenRouter :free → RR по API-ключам.

Путь t2i:
  1. Pollinations Flux (без ключа, вне глобального sem)
  2. Gemini Imagen / Flash (GEMINI_API_KEY[_2])
  3. OpenRouter ``*:free`` только если модель не снята с каталога
  4. RR: GEMINI → OPENROUTER (если включён)

i2i:
  1. Gemini Flash Image (reference)
  2. OpenRouter :free (если включён)

``Semaphore`` только на ключевые API; Pollinations не блокирует всю очередь.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import Any, Literal, TypedDict

import httpx

from config import settings
from services import metrics
from services.api_resilience import ExternalApiError
from services.gemini_image_client import GeminiImageResult, generate_gemini_image_with_reference
from services.pollinations_client import generate_flux_schnell_image

logger = logging.getLogger(__name__)

_FREE_IMAGE_SEM: asyncio.Semaphore | None = None
_B64_URL_RE = re.compile(r"data:image/[^;]+;base64,([A-Za-z0-9+/=]+)")

DEFAULT_OPENROUTER_FLUX_FREE = "black-forest-labs/flux-1-schnell:free"
# Сняты с OpenRouter — не вызываем (404 / no image output).
DEPRECATED_OPENROUTER_FREE_IMAGE_MODELS = frozenset(
    {
        DEFAULT_OPENROUTER_FLUX_FREE,
        "black-forest-labs/flux-1-schnell",
    }
)
DEFAULT_GEMINI_NANO_BANANA = "imagen-3.0-generate-002"
DEFAULT_GEMINI_I2I_MODEL = "gemini-2.5-flash-image"
_GEMINI_T2I_FALLBACK_MODELS = (
    "imagen-4.0-fast-generate-001",
    DEFAULT_GEMINI_NANO_BANANA,
)

# До 1 основной + 3 смещения внутри пула при 429/403/402.
_FAILOVER_SHIFTS = 3
_POST_REQUEST_PAUSE_SEC = 2.0
_ERR_CLIP = 200


def _clip_err(text: object, *, limit: int = _ERR_CLIP) -> str:
    raw = str(text or "").replace("\x00", " ").strip() or "unknown"
    if len(raw) <= limit:
        return raw
    return raw[: max(1, limit - 3)].rstrip() + "..."

ProviderType = Literal["gemini", "openrouter"]


class ProviderSlot(TypedDict):
    type: ProviderType
    key: str


class FreeImageCascadeExhausted(ExternalApiError):
    """Все слоты пула недоступны или исчерпали лимит."""


class OpenRouterPaidBlockedError(ExternalApiError):
    """Попытка уйти на платную модель OpenRouter — запрещено для FREE."""


# ─── Глобальный индекс Round-Robin (in-process) ─────────────────────────────

global_provider_index: int = 0
_provider_index_lock = asyncio.Lock()


def build_free_image_providers() -> list[ProviderSlot]:
    """Упорядоченный пул из 4 ключей; пустые отфильтрованы."""
    raw: list[tuple[ProviderType, str]] = [
        ("gemini", (getattr(settings, "gemini_api_key", None) or "").strip()),
        ("gemini", (getattr(settings, "gemini_api_key_2", None) or "").strip()),
        ("openrouter", (getattr(settings, "openrouter_key", None) or "").strip()),
        ("openrouter", (getattr(settings, "openrouter_key_2", None) or "").strip()),
    ]
    return [{"type": t, "key": k} for t, k in raw if k]


async def _peek_provider_index(n: int) -> int:
    """Текущий слот: ``providers[global_provider_index % n]`` (без сдвига)."""
    async with _provider_index_lock:
        return global_provider_index % max(1, n)


async def _advance_provider_index() -> None:
    """+1 после каждого обращения к API (успех или ошибка)."""
    global global_provider_index
    async with _provider_index_lock:
        global_provider_index += 1


def reset_free_image_rr_for_tests() -> None:
    """Только тесты: обнулить индекс и семафор."""
    global global_provider_index, _FREE_IMAGE_SEM
    global_provider_index = 0
    _FREE_IMAGE_SEM = None


def _semaphore() -> asyncio.Semaphore:
    global _FREE_IMAGE_SEM
    if _FREE_IMAGE_SEM is None:
        # Строго 1: запросы идут по одному, RR по ключам.
        limit = max(1, int(getattr(settings, "free_image_semaphore_limit", 1) or 1))
        _FREE_IMAGE_SEM = asyncio.Semaphore(limit)
    return _FREE_IMAGE_SEM


def reset_free_image_semaphore_for_tests() -> None:
    reset_free_image_rr_for_tests()


def ensure_openrouter_free_model(model: str) -> str:
    """Жёстко требует суффикс ``:free``."""
    raw = (model or "").strip()
    if not raw:
        return DEFAULT_OPENROUTER_FLUX_FREE
    base = raw.split(":", 1)[0].strip()
    if not base:
        return DEFAULT_OPENROUTER_FLUX_FREE
    if raw in DEPRECATED_OPENROUTER_FREE_IMAGE_MODELS:
        raise OpenRouterPaidBlockedError(
            "OpenRouter",
            f"deprecated free image model: {raw}",
        )
    if ":" in raw and not raw.endswith(":free"):
        raise OpenRouterPaidBlockedError(
            "OpenRouter",
            f"paid/non-free model blocked for FREE Nano Banana: {raw}",
        )
    if not raw.endswith(":free"):
        return f"{base}:free"
    return raw


def openrouter_free_image_enabled() -> bool:
    """OpenRouter :free image-модели на каталоге почти не осталось — включаем только явно."""
    model = (getattr(settings, "free_image_openrouter_model", None) or "").strip()
    if not model or model in DEPRECATED_OPENROUTER_FREE_IMAGE_MODELS:
        return False
    return model.endswith(":free")


def _gemini_i2i_model() -> str:
    return (
        getattr(settings, "free_image_gemini_i2i_model", None)
        or DEFAULT_GEMINI_I2I_MODEL
    ).strip() or DEFAULT_GEMINI_I2I_MODEL


def _gemini_t2i_models() -> list[str]:
    primary = (
        getattr(settings, "free_image_gemini_model", None) or DEFAULT_GEMINI_NANO_BANANA
    ).strip() or DEFAULT_GEMINI_NANO_BANANA
    out: list[str] = []
    for mid in (primary, *_GEMINI_T2I_FALLBACK_MODELS):
        if mid and mid not in out:
            out.append(mid)
    return out


def _is_rate_limit_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        x in msg
        for x in (
            "http 429",
            "http 403",
            "http 402",
            "429",
            "403",
            "402",
            "resource_exhausted",
            "rate limit",
            "quota",
        )
    )


def _extract_b64_from_payload(payload: dict[str, Any]) -> bytes | None:
    choices = payload.get("choices") or []
    if choices:
        msg = (choices[0].get("message") or {}) if isinstance(choices[0], dict) else {}
        for img in msg.get("images") or []:
            if not isinstance(img, dict):
                continue
            url = ((img.get("image_url") or {}).get("url") or img.get("url") or "")
            if isinstance(url, str) and url.startswith("data:"):
                m = _B64_URL_RE.search(url)
                if m:
                    return base64.b64decode(m.group(1))
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "image_url":
                    url = (part.get("image_url") or {}).get("url") or ""
                    if isinstance(url, str) and "base64," in url:
                        m = _B64_URL_RE.search(url)
                        if m:
                            return base64.b64decode(m.group(1))
                inline = part.get("inline_data") or part.get("inlineData") or {}
                raw = inline.get("data")
                if raw:
                    return base64.b64decode(raw)
        if isinstance(content, str):
            m = _B64_URL_RE.search(content)
            if m:
                return base64.b64decode(m.group(1))
    data_list = payload.get("data") or []
    for item in data_list:
        if not isinstance(item, dict):
            continue
        b64 = item.get("b64_json") or item.get("b64")
        if b64:
            return base64.b64decode(b64)
    return None


def _build_user_content(
    prompt: str,
    *,
    reference_image_bytes: bytes | None,
    reference_mime: str,
) -> list[dict[str, Any]] | str:
    """OpenRouter chat content: текст отдельно, картинка — data-URL (base64)."""
    if isinstance(prompt, (bytes, bytearray, memoryview)):
        raise ExternalApiError(
            "NanoBanana",
            "prompt must be str, got binary (use reference_image_bytes)",
        )
    text = (prompt or "").strip()
    if not text:
        raise ExternalApiError("NanoBanana", "пустой промпт")
    if len(text) > 8_000:
        raise ExternalApiError(
            "NanoBanana",
            f"prompt suspiciously long ({len(text)} chars); refusing binary/text mix",
        )
    if not reference_image_bytes:
        return text

    if isinstance(reference_image_bytes, memoryview):
        raw = reference_image_bytes.tobytes()
    elif isinstance(reference_image_bytes, (bytes, bytearray)):
        raw = bytes(reference_image_bytes)
    else:
        raise ExternalApiError(
            "NanoBanana",
            f"reference_image_bytes must be bytes, got {type(reference_image_bytes).__name__}",
        )
    if not raw:
        raise ExternalApiError("NanoBanana", "пустой reference image")
    # ~4MB raw → ~5.3MB base64; выше — типичный 400 у провайдера.
    if len(raw) > 4 * 1024 * 1024:
        raise ExternalApiError(
            "NanoBanana",
            f"reference image too large ({len(raw)} bytes)",
        )

    b64 = base64.b64encode(raw).decode("ascii")
    mime = (reference_mime or "image/jpeg").strip() or "image/jpeg"
    if mime in ("jpg", "jpeg", "image/jpg"):
        mime = "image/jpeg"
    elif mime == "png":
        mime = "image/png"
    return [
        {"type": "text", "text": text},
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        },
    ]


def _openrouter_modalities(model: str) -> list[str]:
    """Flux/SD — обычно только image; Gemini-подобные — image+text."""
    m = (model or "").lower()
    if any(
        x in m
        for x in (
            "flux",
            "stable-diffusion",
            "sdxl",
            "hyper-flux",
            "imagen",
            "dreamshaper",
            "playground",
        )
    ):
        return ["image"]
    return ["image", "text"]


def _is_imagen_model(model: str) -> bool:
    m = (model or "").strip().lower()
    return m.startswith("imagen-") or m.startswith("imagen.")


async def _call_openrouter(
    prompt: str,
    *,
    api_key: str,
    reference_image_bytes: bytes | None,
    reference_mime: str,
    timeout: float,
    model: str | None = None,
) -> GeminiImageResult:
    provider = "OpenRouter"
    model = ensure_openrouter_free_model(
        (model or settings.free_image_openrouter_model or DEFAULT_OPENROUTER_FLUX_FREE).strip()
    )
    content = _build_user_content(
        prompt,
        reference_image_bytes=reference_image_bytes,
        reference_mime=reference_mime,
    )
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "modalities": _openrouter_modalities(model),
        "provider": {
            "allow_fallbacks": False,
            "require_parameters": True,
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://neuromule.bot",
        "X-Title": "NeuroMule Flux FREE",
    }
    url = (settings.openrouter_chat_url or "https://openrouter.ai/api/v1/chat/completions").strip()

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
            resp = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException as exc:
        raise ExternalApiError(provider, "timeout") from exc
    except httpx.HTTPError as exc:
        raise ExternalApiError(provider, str(exc)) from exc

    if resp.status_code != 200:
        raise ExternalApiError(provider, f"HTTP {resp.status_code}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise ExternalApiError(provider, "invalid JSON") from exc

    used = str(payload.get("model") or "").strip()
    if used and ":free" not in used.lower():
        raise OpenRouterPaidBlockedError(
            provider,
            f"response model is not :free ({used}); refusing paid billing",
        )

    data = _extract_b64_from_payload(payload)
    if data:
        metrics.incr("free_image.cascade.ok", labels={"provider": provider})
        return GeminiImageResult(data=data)
    raise ExternalApiError(provider, "no image in response")


async def _try_pollinations_flux(prompt: str) -> GeminiImageResult:
    """Основная линия Flux FREE — Pollinations (без API-ключа)."""
    result = await generate_flux_schnell_image(prompt)
    metrics.incr("free_image.cascade.ok", labels={"provider": "pollinations"})
    return result


async def _try_openrouter_spare_wheel(
    prompt: str,
    *,
    reference_image_bytes: bytes | None,
    reference_mime: str,
    timeout: float,
) -> GeminiImageResult:
    """Запасное колесо OpenRouter — только если ``openrouter_free_image_enabled()``."""
    if not openrouter_free_image_enabled():
        raise ExternalApiError(
            "OpenRouter",
            "free image model disabled (no :free image models on catalog)",
        )
    or_model = ensure_openrouter_free_model(
        (settings.free_image_openrouter_model or DEFAULT_OPENROUTER_FLUX_FREE).strip()
    )
    or_slots = [p for p in build_free_image_providers() if p["type"] == "openrouter"]
    if not or_slots:
        raise ExternalApiError(
            "OpenRouter",
            "spare wheel needs OPENROUTER_API_KEY[_2]",
        )
    last_err: object = "unknown"
    for slot in or_slots:
        label = f"openrouter:...{slot['key'][-6:]}"
        try:
            logger.info(
                "Flux FREE spare wheel → %s model=%s",
                label,
                or_model,
            )
            result = await _call_openrouter(
                prompt,
                api_key=slot["key"],
                reference_image_bytes=reference_image_bytes,
                reference_mime=reference_mime,
                timeout=timeout,
                model=or_model,
            )
            metrics.incr("free_image.spare_wheel.ok", labels={"provider": "openrouter"})
            return result
        except OpenRouterPaidBlockedError:
            raise
        except ExternalApiError as exc:
            last_err = _clip_err(exc)
            metrics.incr(
                "free_image.spare_wheel.fail",
                labels={"provider": "openrouter"},
            )
            logger.warning("Flux FREE spare wheel %s failed: %s", label, last_err)
            continue
    raise ExternalApiError("OpenRouter", f"spare wheel exhausted: {last_err}")


async def _try_gemini_spare_wheel(
    prompt: str,
    *,
    reference_mime: str,
    timeout: float,
) -> GeminiImageResult:
    """Запас после Pollinations: Google Imagen / Flash (t2i)."""
    gemini_slots = [p for p in build_free_image_providers() if p["type"] == "gemini"]
    if not gemini_slots:
        raise ExternalApiError("Gemini", "spare wheel needs GEMINI_API_KEY[_2]")
    models = _gemini_t2i_models()
    last_err: object = "unknown"
    for slot in gemini_slots:
        label = f"gemini:...{slot['key'][-6:]}"
        for model in models:
            try:
                logger.info("Flux FREE Gemini spare → %s model=%s", label, model)
                result = await _call_gemini(
                    prompt,
                    api_key=slot["key"],
                    reference_image_bytes=None,
                    reference_mime=reference_mime,
                    timeout=timeout,
                    model=model,
                )
                metrics.incr("free_image.spare_wheel.ok", labels={"provider": "gemini"})
                return result
            except ExternalApiError as exc:
                last_err = _clip_err(exc)
                metrics.incr(
                    "free_image.spare_wheel.fail",
                    labels={"provider": "gemini"},
                )
                logger.warning(
                    "Flux FREE Gemini spare %s model=%s failed: %s",
                    label,
                    model,
                    last_err,
                )
                continue
    raise ExternalApiError("Gemini", f"gemini spare exhausted: {last_err}")


async def _try_gemini_i2i_spare_wheel(
    prompt: str,
    *,
    reference_image_bytes: bytes,
    reference_mime: str,
    timeout: float,
) -> GeminiImageResult:
    """i2i: Gemini Flash Image с reference (Imagen reference не принимает)."""
    gemini_slots = [p for p in build_free_image_providers() if p["type"] == "gemini"]
    if not gemini_slots:
        raise ExternalApiError("Gemini", "i2i spare needs GEMINI_API_KEY[_2]")
    model = _gemini_i2i_model()
    last_err: object = "unknown"
    for slot in gemini_slots:
        label = f"gemini:...{slot['key'][-6:]}"
        try:
            logger.info("Flux FREE Gemini i2i → %s model=%s", label, model)
            result = await _call_gemini(
                prompt,
                api_key=slot["key"],
                reference_image_bytes=reference_image_bytes,
                reference_mime=reference_mime,
                timeout=timeout,
                model=model,
            )
            metrics.incr("free_image.spare_wheel.ok", labels={"provider": "gemini_i2i"})
            return result
        except ExternalApiError as exc:
            last_err = _clip_err(exc)
            metrics.incr(
                "free_image.spare_wheel.fail",
                labels={"provider": "gemini_i2i"},
            )
            logger.warning("Flux FREE Gemini i2i %s failed: %s", label, last_err)
            continue
    raise ExternalApiError("Gemini", f"gemini i2i spare exhausted: {last_err}")


async def _call_gemini(
    prompt: str,
    *,
    api_key: str,
    reference_image_bytes: bytes | None,
    reference_mime: str,
    timeout: float,
    model: str | None = None,
) -> GeminiImageResult:
    provider = "Gemini"
    mid = (model or settings.free_image_gemini_model or DEFAULT_GEMINI_NANO_BANANA).strip()
    if not mid:
        mid = DEFAULT_GEMINI_NANO_BANANA

    if reference_image_bytes and _is_imagen_model(mid):
        raise ExternalApiError(
            provider,
            "imagen model skipped for image-to-image",
        )

    try:
        async with asyncio.timeout(timeout):
            if _is_imagen_model(mid):
                from services.gemini_image_client import generate_imagen_model

                result = await generate_imagen_model(
                    prompt,
                    mid,
                    api_key=api_key,
                )
            else:
                result = await generate_gemini_image_with_reference(
                    prompt,
                    mid,
                    reference_image_bytes=reference_image_bytes,
                    reference_mime=reference_mime,
                    api_key=api_key,
                )
    except TimeoutError as exc:
        raise ExternalApiError(provider, "timeout") from exc
    except RuntimeError as exc:
        raise ExternalApiError(provider, str(exc)) from exc

    if result.has_image():
        metrics.incr("free_image.cascade.ok", labels={"provider": provider})
        return result
    raise ExternalApiError(provider, "no image in response")


async def _invoke_slot(
    slot: ProviderSlot,
    prompt: str,
    *,
    reference_image_bytes: bytes | None,
    reference_mime: str,
    timeout: float,
) -> GeminiImageResult:
    if slot["type"] == "gemini":
        model = _gemini_i2i_model() if reference_image_bytes else None
        return await _call_gemini(
            prompt,
            api_key=slot["key"],
            reference_image_bytes=reference_image_bytes,
            reference_mime=reference_mime,
            timeout=timeout,
            model=model,
        )
    if not openrouter_free_image_enabled():
        raise ExternalApiError("OpenRouter", "free image model disabled")
    return await _call_openrouter(
        prompt,
        api_key=slot["key"],
        reference_image_bytes=reference_image_bytes,
        reference_mime=reference_mime,
        timeout=timeout,
    )


def _providers_for_request(
    providers: list[ProviderSlot],
    *,
    has_reference: bool,
) -> list[ProviderSlot]:
    """i2i → Gemini Flash + (опц.) OpenRouter; t2i → весь пул без OR если выключен."""
    if has_reference:
        out = [p for p in providers if p["type"] == "gemini"]
        if openrouter_free_image_enabled():
            out.extend(p for p in providers if p["type"] == "openrouter")
        return out
    if openrouter_free_image_enabled():
        return providers
    return [p for p in providers if p["type"] == "gemini"]


async def _run_keyed_api_step(coro):
    """Семафор только на платные/ключевые API — Pollinations не ждёт в очереди."""
    async with _semaphore():
        try:
            return await coro
        finally:
            pause = float(
                getattr(settings, "free_image_key_pause_sec", _POST_REQUEST_PAUSE_SEC)
                or _POST_REQUEST_PAUSE_SEC
            )
            await asyncio.sleep(max(0.0, pause))


async def generate_free_tier_image(
    prompt: str,
    *,
    reference_image_bytes: bytes | None = None,
    reference_mime: str = "image/jpeg",
) -> GeminiImageResult:
    """
    Flux FREE — неубиваемый путь:

    1. Pollinations Flux (legacy / gen, без глобального sem);
    2. Gemini Imagen/Flash spare (t2i) или Gemini Flash i2i (reference);
    3. OpenRouter ``*:free`` — только если модель явно включена и не deprecated;
    4. RR по пулу ключей как последний рубеж.

    Raises:
        FreeImageCascadeExhausted: все линии недоступны.
    """
    text = str(prompt or "").strip() or "Улучши это фото"
    has_ref = bool(reference_image_bytes)
    timeout = min(float(settings.free_image_cascade_timeout_sec or 90.0), 90.0)
    last_err = "unknown"

    try:
        async with asyncio.timeout(180.0):
            # ── 1. Pollinations (только text-to-image, вне sem) ─────────────
            if not has_ref:
                try:
                    logger.info("Flux FREE primary → Pollinations")
                    return await _try_pollinations_flux(text)
                except (ExternalApiError, TimeoutError) as exc:
                    last_err = _clip_err(exc)
                    metrics.incr(
                        "free_image.cascade.fail",
                        labels={"provider": "pollinations", "reason": "error"},
                    )
                    logger.warning(
                        "Pollinations failed, Gemini spare: %s",
                        last_err,
                    )

                # ── 2. Gemini Imagen/Flash spare (t2i) ─────────────────────
                try:
                    return await _run_keyed_api_step(
                        _try_gemini_spare_wheel(
                            text,
                            reference_mime=reference_mime,
                            timeout=timeout,
                        )
                    )
                except ExternalApiError as exc:
                    last_err = _clip_err(exc)
                    logger.warning("Gemini t2i spare failed: %s", last_err)

            # ── 2b. Gemini i2i spare ───────────────────────────────────────
            if has_ref and reference_image_bytes is not None:
                try:
                    return await _run_keyed_api_step(
                        _try_gemini_i2i_spare_wheel(
                            text,
                            reference_image_bytes=reference_image_bytes,
                            reference_mime=reference_mime,
                            timeout=timeout,
                        )
                    )
                except ExternalApiError as exc:
                    last_err = _clip_err(exc)
                    logger.warning("Gemini i2i spare failed: %s", last_err)

            # ── 3. OpenRouter :free (если включён) ─────────────────────────
            if openrouter_free_image_enabled():
                try:
                    return await _run_keyed_api_step(
                        _try_openrouter_spare_wheel(
                            text,
                            reference_image_bytes=reference_image_bytes,
                            reference_mime=reference_mime,
                            timeout=timeout,
                        )
                    )
                except OpenRouterPaidBlockedError as exc:
                    last_err = _clip_err(exc)
                    logger.error("Flux FREE OR spare paid-block: %s", last_err)
                except ExternalApiError as exc:
                    last_err = _clip_err(exc)
                    logger.warning("OpenRouter spare exhausted, RR cascade: %s", last_err)
            else:
                logger.info("Flux FREE: OpenRouter spare skipped (no enabled :free image model)")

            # ── 4. RR last resort ───────────────────────────────────────────
            all_providers = build_free_image_providers()
            providers = _providers_for_request(all_providers, has_reference=has_ref)
            if not providers:
                raise FreeImageCascadeExhausted(
                    "FluxFreeCascade",
                    last_err
                    if last_err != "unknown"
                    else "no API keys for spare wheel / RR",
                )

            n = len(providers)
            max_attempts = min(n, 1 + _FAILOVER_SHIFTS)

            async def _rr_attempt() -> GeminiImageResult:
                nonlocal last_err
                for shift in range(max_attempts):
                    idx = await _peek_provider_index(n)
                    slot = providers[idx]
                    label = f"{slot['type']}:...{slot['key'][-6:]}"
                    try:
                        logger.info(
                            "Flux FREE RR idx=%s shift=%s slot=%s pool=%s ref=%s",
                            idx,
                            shift,
                            label,
                            n,
                            has_ref,
                        )
                        result = await _invoke_slot(
                            slot,
                            text,
                            reference_image_bytes=reference_image_bytes,
                            reference_mime=reference_mime,
                            timeout=timeout,
                        )
                        metrics.incr(
                            "free_image.rr",
                            labels={"type": slot["type"], "shift": str(shift)},
                        )
                        return result
                    except ExternalApiError as exc:
                        last_err = _clip_err(exc)
                        metrics.incr(
                            "free_image.cascade.fail",
                            labels={"provider": slot["type"], "reason": "error"},
                        )
                        if isinstance(exc, OpenRouterPaidBlockedError):
                            break
                        if _is_rate_limit_error(exc) and shift + 1 < max_attempts:
                            logger.warning(
                                "Flux FREE failover %s → next (%s)",
                                label,
                                last_err,
                            )
                            continue
                        break
                    finally:
                        await _advance_provider_index()
                raise FreeImageCascadeExhausted("FluxFreeCascade", last_err)

            return await _run_keyed_api_step(_rr_attempt())
    except FreeImageCascadeExhausted:
        raise
    except TimeoutError as err:
        metrics.incr("free_image.cascade.exhausted")
        logger.error(
            "Критический сбой бесплатного каскада. Мул завис. Ошибка: %s",
            err,
        )
        raise FreeImageCascadeExhausted(
            "FluxFreeCascade",
            "hard timeout 180s",
        ) from err

    metrics.incr("free_image.cascade.exhausted")
    raise FreeImageCascadeExhausted("FluxFreeCascade", _clip_err(last_err))
