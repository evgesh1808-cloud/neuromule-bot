"""POST /api/webapp/generate — Mini App → billing → fire_photo_job."""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from config import settings
from content import messages as msg
from ports.webapp_auth import (
    TelegramInitDataError,
    VkLaunchParamsError,
    WebAppAuthContext,
    resolve_webapp_auth_from_headers,
)
from services.agent_intent import _resolve_model_from_key
from services.agent_intent_dispatch import run_webapp_image_pipeline
from services.photo_aspect_ratio import openrouter_aspect_ratio
from services.use_cases.photo_generation_turn import PhotoGenOutcome

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webapp", tags=["webapp"])


class WebAppGenerateRequest(BaseModel):
    user_id: int = Field(..., gt=0)
    platform: Literal["telegram", "vk"]
    model_key: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    aspect_ratio: str = Field(default="1:1", max_length=16)
    prompt: str = Field(..., min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _require_model(self) -> WebAppGenerateRequest:
        if not (self.model_key or self.model):
            self.model_key = "nano_banana2"
        return self


async def require_webapp_auth(
    authorization: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
    x_vk_launch_params: Annotated[str | None, Header(alias="X-VK-Launch-Params")] = None,
) -> WebAppAuthContext:
    try:
        return resolve_webapp_auth_from_headers(
            authorization=authorization,
            x_telegram_init_data=x_telegram_init_data,
            x_vk_launch_params=x_vk_launch_params,
        )
    except TelegramInitDataError as exc:
        logger.debug("webapp auth rejected (TG): %s", exc)
        raise HTTPException(status_code=401, detail="Unauthorized") from exc
    except VkLaunchParamsError as exc:
        logger.debug("webapp auth rejected (VK): %s", exc)
        raise HTTPException(status_code=401, detail="Unauthorized") from exc


def _resolve_model(raw_key: str | None, raw_legacy: str | None) -> tuple[str, str, str]:
    source = (raw_key or raw_legacy or "nano_banana2").strip()
    enum_key, model_id, model_label = _resolve_model_from_key(source)
    allowed = {"nano_banana2", "flux_schnell", "dalle_3"}
    if model_id not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported model_key")
    return enum_key, model_id, model_label


def _outcome_to_http(outcome: PhotoGenOutcome, detail: str | None = None) -> HTTPException:
    mapping = {
        PhotoGenOutcome.INSUFFICIENT_BALANCE: (402, detail or msg.TXT_INSUFFICIENT_BALANCE),
        PhotoGenOutcome.DAILY_LIMIT_EXCEEDED: (
            429,
            msg.TXT_PHOTO_DAILY_LIMIT.format(limit=settings.free_daily_photo_limit),
        ),
        PhotoGenOutcome.GLOBAL_FREE_IMAGE_CAP: (429, msg.TXT_FREE_IMAGE_GLOBAL_CAP),
        PhotoGenOutcome.FREE_IMAGE_MODEL_BLOCKED: (403, msg.TXT_FREE_IMAGE_MODEL_BLOCKED),
        PhotoGenOutcome.NEED_PROMPT: (400, msg.TXT_CREATE_IMAGE_AFTER_MODEL),
    }
    status, message = mapping.get(outcome, (500, msg.TXT_GEN_JOB_FAILED))
    return HTTPException(status_code=status, detail=message)


@router.post("/generate")
async def webapp_generate_image(
    body: WebAppGenerateRequest,
    auth: Annotated[WebAppAuthContext, Depends(require_webapp_auth)],
    request: Request,
) -> dict[str, str]:
    if body.user_id != auth.user_id or body.platform != auth.platform:
        raise HTTPException(status_code=403, detail="user_id/platform mismatch")

    _model_key, model_id, model_label = _resolve_model(body.model_key, body.model)
    aspect = openrouter_aspect_ratio(body.aspect_ratio)
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Empty prompt")

    bot = getattr(request.app.state, "tg_bot", None)
    outcome, refusal = await run_webapp_image_pipeline(
        platform=auth.platform,
        user_id=auth.user_id,
        chat_id=auth.chat_id,
        model_id=model_id,
        model_label=model_label,
        prompt=prompt,
        aspect_ratio=aspect,
        bot=bot,
    )
    if outcome is not PhotoGenOutcome.SUCCESS:
        raise _outcome_to_http(outcome, refusal)

    return {"status": "ok"}
