"""Аутентификация Telegram Mini App через валидацию ``initData`` (HMAC-SHA256)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import parse_qsl, unquote

from fastapi import Header, HTTPException

from config import settings

logger = logging.getLogger(__name__)

_AUTH_SCHEME = "tma"
_DEFAULT_MAX_AGE_SEC = 86_400  # 24 ч — защита от replay старых initData


@dataclass(frozen=True)
class TelegramWebAppUser:
    """Пользователь, извлечённый из валидного ``initData``."""

    telegram_id: int
    auth_date: int
    raw_user: dict[str, object]


class TelegramInitDataError(ValueError):
    """Невалидная или просроченная строка initData."""


def _bot_token() -> str:
    token = (settings.tg_token or "").strip()
    if not token:
        raise TelegramInitDataError("TG_TOKEN is not configured")
    return token


def _max_age_sec() -> int:
    raw = getattr(settings, "mini_app_init_data_max_age_sec", _DEFAULT_MAX_AGE_SEC)
    try:
        age = int(raw)
    except (TypeError, ValueError):
        age = _DEFAULT_MAX_AGE_SEC
    return max(60, age)


def _parse_init_data_pairs(init_data: str) -> dict[str, str]:
    cleaned = (init_data or "").strip()
    if not cleaned:
        raise TelegramInitDataError("initData is empty")
    pairs = dict(parse_qsl(cleaned, keep_blank_values=True, strict_parsing=True))
    if not pairs:
        raise TelegramInitDataError("initData has no fields")
    return pairs


def _compute_init_data_hash(bot_token: str, fields: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


def validate_telegram_init_data(init_data: str) -> TelegramWebAppUser:
    """
    Проверяет подпись Telegram Web App ``initData`` и возвращает ``telegram_id``.

    Алгоритм: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    pairs = _parse_init_data_pairs(init_data)
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise TelegramInitDataError("initData missing hash")

    bot_token = _bot_token()
    calculated_hash = _compute_init_data_hash(bot_token, pairs)
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise TelegramInitDataError("initData HMAC mismatch")

    auth_date_raw = pairs.get("auth_date", "").strip()
    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise TelegramInitDataError("initData auth_date invalid") from exc

    now = int(time.time())
    if auth_date > now + 300:
        raise TelegramInitDataError("initData auth_date is in the future")
    if now - auth_date > _max_age_sec():
        raise TelegramInitDataError("initData expired")

    user_raw = pairs.get("user", "").strip()
    if not user_raw:
        raise TelegramInitDataError("initData missing user")

    try:
        user_obj = json.loads(unquote(user_raw))
    except json.JSONDecodeError as exc:
        raise TelegramInitDataError("initData user JSON invalid") from exc

    if not isinstance(user_obj, dict):
        raise TelegramInitDataError("initData user must be an object")

    telegram_id = user_obj.get("id")
    if not isinstance(telegram_id, int) or telegram_id <= 0:
        raise TelegramInitDataError("initData user.id invalid")

    return TelegramWebAppUser(
        telegram_id=telegram_id,
        auth_date=auth_date,
        raw_user=user_obj,
    )


def extract_init_data_from_headers(
    *,
    authorization: str | None,
    x_telegram_init_data: str | None,
) -> str:
    """Читает initData из ``Authorization: tma <data>`` или ``X-Telegram-Init-Data``."""
    if x_telegram_init_data and x_telegram_init_data.strip():
        return x_telegram_init_data.strip()

    if authorization:
        scheme, _, remainder = authorization.partition(" ")
        if scheme.lower() == _AUTH_SCHEME and remainder.strip():
            return remainder.strip()

    raise TelegramInitDataError("Missing Telegram initData header")


async def require_telegram_user(
    authorization: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
) -> int:
    """
    FastAPI Dependency: валидирует initData и возвращает нативный ``telegram_id``.

    Клиент Mini App должен передавать:
    - ``Authorization: tma <Telegram.WebApp.initData>``, или
    - ``X-Telegram-Init-Data: <Telegram.WebApp.initData>``.
    """
    try:
        init_data = extract_init_data_from_headers(
            authorization=authorization,
            x_telegram_init_data=x_telegram_init_data,
        )
        user = validate_telegram_init_data(init_data)
        return user.telegram_id
    except TelegramInitDataError as exc:
        logger.debug("Telegram initData rejected: %s", exc)
        raise HTTPException(status_code=401, detail="Unauthorized") from exc


def sign_init_data_for_tests(
    bot_token: str,
    *,
    user_id: int,
    auth_date: int | None = None,
    extra_fields: dict[str, str] | None = None,
) -> str:
    """
    Собирает подписанную строку initData для unit/integration тестов.

    Не использовать в production-коде.
    """
    from urllib.parse import urlencode

    ts = auth_date if auth_date is not None else int(time.time())
    fields: dict[str, str] = {
        "auth_date": str(ts),
        "user": json.dumps({"id": user_id}, separators=(",", ":")),
    }
    if extra_fields:
        fields.update(extra_fields)
    digest = _compute_init_data_hash(bot_token, dict(fields))
    fields["hash"] = digest
    return urlencode(fields)
