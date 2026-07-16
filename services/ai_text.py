"""
Асинхронные вызовы OpenRouter (Chat Completions).

Каждый запрос передаёт ``max_tokens`` из конфига (ограничение стоимости ответа).
При переданном ``stream_callback`` включается режим SSE: текст накапливается по дельтам для live-редактирования в Telegram.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, TypedDict

import httpx

from config import Settings
from services.openrouter_http import get_openrouter_http_client

logger = logging.getLogger(__name__)

# Колбэк стриминга: (накопленный_текст, завершено_ли_сообщение).
StreamCallback = Callable[[str, bool], Awaitable[None]]


class ChatCompletionResult(TypedDict):
    """Ответ OpenRouter Chat Completions с usage для финансовой аналитики."""

    content: str
    prompt_tokens: int
    completion_tokens: int


def _extract_usage_tokens(data: Any) -> tuple[int, int]:
    """Безопасно извлекает ``prompt_tokens`` / ``completion_tokens`` из ``usage``."""
    try:
        if not isinstance(data, dict):
            return 0, 0
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return 0, 0
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        return max(prompt, 0), max(completion, 0)
    except (TypeError, ValueError, AttributeError):
        logger.debug("OpenRouter usage parse failed", exc_info=True)
        return 0, 0


def _build_completion_result(
    content: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> ChatCompletionResult:
    return {
        "content": content,
        "prompt_tokens": max(int(prompt_tokens or 0), 0),
        "completion_tokens": max(int(completion_tokens or 0), 0),
    }


def _estimate_messages_chars(messages: list[dict[str, Any]]) -> int:
    """Суммирует длину полей content — грубая защита от гигантского JSON."""
    n = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    n += len(str(part))
                    continue
                if part.get("type") == "text":
                    n += len(str(part.get("text", "")))
                elif part.get("type") == "image_url":
                    url = (part.get("image_url") or {}).get("url", "")
                    n += len(str(url))
                else:
                    n += len(str(part))
        else:
            n += len(str(content))
    return n


def _messages_contain_image(messages: list[dict[str, Any]]) -> bool:
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                return True
    return False


def _estimate_messages_prompt_tokens_chars(messages: list[dict[str, Any]], char_per_token: int) -> int:
    if char_per_token <= 0:
        char_per_token = 3
    total = 0
    for m in messages:
        total += len(_message_content_as_text(m.get("content"))) // char_per_token
    return total


def _message_content_as_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                t = item.get("text")
                if isinstance(t, str):
                    parts.append(t)
                elif item.get("type") == "image_url":
                    parts.append("[image]")
        return "".join(parts)
    return str(content)


def estimate_messages_prompt_tokens(
    messages: list[dict[str, Any]],
    *,
    settings: Settings | None = None,
    char_per_token: int | None = None,
) -> int:
    """
    Оценка входных токенов: при ``settings.chat_use_tiktoken`` — tiktoken по тексту ролей и content;
    иначе сумма ``len(content)//char_per_token`` (эвристика для кириллицы и т.п.).
    """
    cpt = char_per_token if char_per_token is not None else (settings.chat_char_per_token_est if settings else 3)
    if settings is not None and settings.chat_use_tiktoken:
        try:
            import tiktoken

            try:
                enc = tiktoken.encoding_for_model("gpt-3.5-turbo")
            except Exception:
                enc = tiktoken.get_encoding(settings.tiktoken_encoding)
            tokens_per_message = 3
            tokens_per_name = 1
            n = 0
            for m in messages:
                n += tokens_per_message
                for key, value in m.items():
                    if key == "content":
                        val = _message_content_as_text(value)
                    else:
                        val = value
                    n += len(enc.encode(str(val)))
                    if key == "name":
                        n += tokens_per_name
            n += 3
            return n
        except Exception:
            logger.debug("tiktoken count failed, using char heuristic", exc_info=True)
    return _estimate_messages_prompt_tokens_chars(messages, cpt)


def _mask_openrouter_error_body(raw: str | bytes) -> str:
    """Укорачивает тело ошибки OpenRouter, чтобы не утекали фрагменты промптов в лог."""
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw or "")
    snippet = text[:100].replace("\n", " ").replace("\r", " ")
    if len(text) > 100:
        snippet += "…[truncated]"
    return snippet


@asynccontextmanager
async def _http_client_scope(
    http_client: httpx.AsyncClient | None,
    settings: Settings,
) -> AsyncIterator[httpx.AsyncClient]:
    """Отдаёт переданный клиент или переиспользуемый singleton OpenRouter-клиент."""
    if http_client is not None:
        yield http_client
        return
    yield await get_openrouter_http_client(settings)


def get_chat_headers(settings: Settings) -> dict[str, str]:
    """Заголовки авторизации для OpenRouter (Bearer + JSON)."""
    return {
        "Authorization": f"Bearer {settings.openrouter_key}",
        "Content-Type": "application/json",
    }


def _sanitize_openrouter_model_id(model_id: str) -> str:
    """Нормализует model id; суффикс ``:free`` и ``openrouter/free`` сохраняем."""
    return str(model_id or "").strip()


def _build_openrouter_model_chain(model_ids: list[str]) -> list[str]:
    """Собирает каскад моделей без дубликатов после нормализации."""
    out: list[str] = []
    seen: set[str] = set()
    for mid in model_ids:
        clean = _sanitize_openrouter_model_id(mid)
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def _chat_payload(
    settings: Settings,
    model: str,
    messages: list[dict[str, Any]],
    *,
    stream: bool,
    max_tokens: int | None = None,
    response_format: dict[str, Any] | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """
    Собирает тело POST /chat/completions: модель, сообщения, ``max_tokens``, опционально ``stream``.

    ``max_tokens`` из ``ChatRoutePlan``; fallback — ``settings.openrouter_max_output_tokens``.
    """
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens if max_tokens is not None else settings.openrouter_max_output_tokens,
    }
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    if response_format:
        body["response_format"] = response_format
    if temperature is not None:
        body["temperature"] = temperature
    body["extra_body"] = {"prompt_caching": True}
    return body


async def _post_chat_completion(
    client: httpx.AsyncClient,
    settings: Settings,
    model: str,
    messages: list[dict[str, Any]],
    *,
    timeout: float,
    max_tokens: int | None = None,
    response_format: dict[str, Any] | None = None,
    temperature: float | None = None,
) -> ChatCompletionResult | None:
    """Один нестриминговый запрос; при HTTP≠200 или пустом content возвращает ``None``."""
    payload = _chat_payload(
        settings,
        model,
        messages,
        stream=False,
        max_tokens=max_tokens,
        response_format=response_format,
        temperature=temperature,
    )
    response = await client.post(
        settings.openrouter_chat_url,
        headers=get_chat_headers(settings),
        json=payload,
        timeout=timeout,
    )
    if response.status_code == 429:
        # Rate-limit на :free модели — поднимаем явный лог, чтобы было
        # видно в графиках/алертах. Внешний цикл по `model_chain` сам
        # переключится на следующую (резервную) модель.
        logger.warning(
            "OpenRouter model=%s rate_limited (429) — falling back to next model",
            model,
        )
        return None
    if response.status_code != 200:
        logger.warning(
            "OpenRouter model=%s status=%s body=%s",
            model,
            response.status_code,
            _mask_openrouter_error_body(response.text),
        )
        return None
    try:
        data = response.json()
    except Exception:
        logger.warning("OpenRouter model=%s invalid JSON body", model, exc_info=True)
        return None
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("OpenRouter model=%s missing choices/message/content", model)
        return None
    if not isinstance(content, str):
        return None
    prompt_tokens, completion_tokens = _extract_usage_tokens(data)
    return _build_completion_result(
        content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def _stream_delta_text(piece: Any) -> str:
    """Текст из ``delta.content`` (строка, список фрагментов OpenAI/OpenRouter)."""
    if piece is None:
        return ""
    if isinstance(piece, str):
        return piece
    if isinstance(piece, list):
        parts: list[str] = []
        for item in piece:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                t = item.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "".join(parts)
    return str(piece)


async def _iter_sse_data_payloads(resp: httpx.Response) -> AsyncIterator[str]:
    """
    Разбор SSE по байтам (устойчиво к разбиению чанков TCP): строки ``data:...``,
    комментарии ``:``, пустые строки — пропуск.
    """
    buf = b""
    async for chunk in resp.aiter_bytes():
        buf += chunk
        while True:
            nl = buf.find(b"\n")
            if nl == -1:
                break
            line_b, buf = buf[:nl], buf[nl + 1 :]
            line = line_b.decode("utf-8", errors="replace").rstrip("\r")
            if not line:
                continue
            if line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            yield line[5:].lstrip()


async def _post_chat_completion_stream(
    client: httpx.AsyncClient,
    settings: Settings,
    model: str,
    messages: list[dict[str, Any]],
    *,
    timeout: float,
    stream_callback: StreamCallback,
    max_tokens: int | None = None,
    response_format: dict[str, Any] | None = None,
    temperature: float | None = None,
) -> ChatCompletionResult | None:
    """
    Стриминговый запрос (SSE): ``data:`` по байтам, ``delta.content``, колбэк с частичным текстом.

    При ``stream_options.include_usage`` OpenRouter присылает ``usage`` в финальном SSE-чанке.
    """
    payload = _chat_payload(
        settings,
        model,
        messages,
        stream=True,
        max_tokens=max_tokens,
        response_format=response_format,
        temperature=temperature,
    )
    acc = ""
    prompt_tokens = 0
    completion_tokens = 0
    try:
        async with client.stream(
            "POST",
            settings.openrouter_chat_url,
            headers=get_chat_headers(settings),
            json=payload,
            timeout=timeout,
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread())[:800]
                logger.warning(
                    "OpenRouter stream model=%s status=%s body=%s",
                    model,
                    resp.status_code,
                    _mask_openrouter_error_body(body),
                )
                return None
            async for raw in _iter_sse_data_payloads(resp):
                if raw == "[DONE]":
                    if acc:
                        await stream_callback(acc, True)
                    return (
                        _build_completion_result(
                            acc,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                        )
                        if acc
                        else None
                    )
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("OpenRouter stream skip bad JSON: %s", raw[:200])
                    continue
                pt, ct = _extract_usage_tokens(obj)
                if pt or ct:
                    prompt_tokens, completion_tokens = pt, ct
                for ch in obj.get("choices") or []:
                    delta = ch.get("delta") or {}
                    piece = _stream_delta_text(delta.get("content"))
                    if piece:
                        acc += piece
                        await stream_callback(acc, False)
            if acc:
                await stream_callback(acc, True)
            return (
                _build_completion_result(
                    acc,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                if acc
                else None
            )
    except Exception:
        logger.exception("OpenRouter stream model=%s failed", model)
        if acc:
            try:
                await stream_callback(acc, True)
            except Exception:
                logger.debug("stream_callback on error failed", exc_info=True)
        return None


async def ask_ai_messages(
    settings: Settings,
    messages: list[dict[str, Any]],
    *,
    timeout: float | None = None,
    max_context_chars: int = 120_000,
    max_context_tokens: int | None = None,
    char_per_token: int = 3,
    http_client: httpx.AsyncClient | None = None,
    stream_callback: StreamCallback | None = None,
    models: list[str] | None = None,
    max_tokens: int | None = None,
    text_role: str | None = None,
    response_format: dict[str, Any] | None = None,
    temperature: float | None = None,
) -> ChatCompletionResult:
    """
    Отправляет ``messages`` в OpenRouter; перебирает ``free_models`` до успеха.

    Если задан ``stream_callback``, сначала для каждой модели пробуется SSE; при неудаче — обычный POST.

    ``text_role == "table_generator"`` включает JSON Mode: ``response_format: {type: json_object}``.

    Возвращает словарь ``{content, prompt_tokens, completion_tokens}``; при отсутствии ``usage`` токены = 0.
    """
    is_table_role = (text_role or "").strip().lower() == "table_generator"
    if response_format is None and is_table_role:
        response_format = {"type": "json_object"}
    if is_table_role:
        stream_callback = None
    if _estimate_messages_chars(messages) > max_context_chars:
        logger.warning("OpenRouter: context too long (%s chars), aborting", max_context_chars)
        raise RuntimeError("context_too_long")

    if max_context_tokens is not None:
        est = estimate_messages_prompt_tokens(
            messages,
            settings=settings,
            char_per_token=char_per_token,
        )
        if est > max_context_tokens:
            logger.warning(
                "OpenRouter: estimated prompt tokens %s > limit %s",
                est,
                max_context_tokens,
            )
            raise RuntimeError("context_too_long_tokens")

    t = timeout if timeout is not None else settings.openrouter_timeout_sec

    model_chain = _build_openrouter_model_chain(
        [m for m in (models or settings.free_models) if str(m).strip()]
    )
    use_stream = (
        stream_callback is not None
        and not _messages_contain_image(messages)
        and response_format is None
    )
    if stream_callback is not None and not use_stream:
        logger.debug("OpenRouter: multimodal request — streaming disabled")

    async with _http_client_scope(http_client, settings) as client:
        for raw_model in model_chain:
            model = _sanitize_openrouter_model_id(raw_model)
            if not model:
                continue
            try:
                if use_stream:
                    result = await _post_chat_completion_stream(
                        client,
                        settings,
                        model,
                        messages,
                        timeout=t,
                        stream_callback=stream_callback,
                        max_tokens=max_tokens,
                        response_format=response_format,
                        temperature=temperature,
                    )
                    if result is not None and result.get("content"):
                        return result
                    # Stream пустой/упал — сразу следующая модель (не ждём ещё timeout non-stream).
                    logger.warning(
                        "OpenRouter model=%s stream empty/failed — next model",
                        model,
                    )
                    continue
                result = await _post_chat_completion(
                    client,
                    settings,
                    model,
                    messages,
                    timeout=t,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    temperature=temperature,
                )
                if result is not None and result.get("content"):
                    if stream_callback is not None:
                        await stream_callback(result["content"], True)
                    return result
            except Exception:
                logger.exception("OpenRouter model=%s request failed", model)
                continue

    raise RuntimeError("openrouter_unavailable")


async def ask_ai_text(
    settings: Settings,
    prompt: str,
    *,
    timeout_override: float | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Короткий вызов system+user; внутри тот же ``max_tokens`` и лимиты длины, что и в чате."""
    try:
        system = (
            f"Ты — ассистент {settings.bot_name}. Отвечай по-русски, кратко и по делу. "
            "Используй термины «Маршрут», «Системы», «Нейроны». "
            "Не используй слова «груз» и «вьюки». "
            "Не приписывай этому боту функции вроде «аркана дня», Human Design, натальных карт "
            "и подобного: реальные возможности продукта — нейротекст, изображения, оживление фото, "
            "видео, музыка и помощь с текстовым промптом для картинки. "
            "Если пользователь сам просит эзотерику как тему для текста — обсуждай как обычную тему, "
            "но не выдавай это за встроенные разделы бота."
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        completion = await ask_ai_messages(
            settings,
            messages,
            timeout=timeout_override,
            max_context_chars=50_000,
            max_context_tokens=16_000,
            http_client=http_client,
        )
        return completion.get("content") or ""
    except RuntimeError as e:
        if str(e) in ("context_too_long", "context_too_long_tokens"):
            return "Запрос слишком длинный. Сократите текст и попробуйте снова."
        logger.error("ask_ai_text: all models failed or unavailable")
        return "Сервис временно недоступен. Попробуйте через минуту."
    except Exception:
        logger.exception("ask_ai_text unexpected error")
        return "Сервис временно недоступен. Попробуйте через минуту."
