"""Web Browsing для NeuroMule 🐎⚡️ — Фишка 2 виральных премиум-фич.

Если пользователь пишет в чат сообщение с ``http://`` / ``https://`` ссылкой,
бот тянет HTML асинхронно, чистит от тегов / скриптов и подмешивает
извлечённый текст в контекст ИИ. Биллинг прохода — как **экспертный текст**
(5 ⚡ или 3 💎 fallback), список ``services/billing/chat_pipeline.py``.

Стек реализации **намерено лёгкий**: только ``httpx`` + регулярки. Никаких
``bs4`` / ``selectolax`` / ``readability-lxml`` — у NeuroMule нет лицензии на
лишние deps. Точность ~90 % для классических SSR-страниц (новости, статьи,
блоги), что более чем достаточно для саммари ИИ.

API модуля:

* :func:`find_urls` — выдёргивает все валидные http/https URL из текста.
* :func:`fetch_and_clean` — async, скачивает страницу, чистит HTML, режет до
  лимита символов, возвращает чистый текст или ``None`` при ошибке.
* :func:`extract_browsing_context` — high-level helper: ищет URL → fetch'ит
  → склеивает в один cleaned-blob с пометкой ``[web]``.

Все ошибки сети / парсинга гасим и возвращаем ``None`` — пайплайн чата сам
решит, оставить просто текст или перейти на экспертный режим.
"""

from __future__ import annotations

import logging
import re
from typing import Final

import httpx

from services.file_processor import compress_extracted_text

logger = logging.getLogger(__name__)


# ─── Regexes ───────────────────────────────────────────────────────────────


_URL_RE: Final = re.compile(
    r"https?://(?:[A-Za-z0-9_\-]+\.)+[A-Za-z]{2,}(?:[/?#][^\s<>\"']*)?",
    re.IGNORECASE,
)
_SCRIPT_STYLE_RE: Final = re.compile(
    r"<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_HEAD_RE: Final = re.compile(r"<head\b[^>]*>.*?</head\s*>", re.IGNORECASE | re.DOTALL)
_TAG_RE: Final = re.compile(r"<[^>]+>")
_HTML_COMMENT_RE: Final = re.compile(r"<!--.*?-->", re.DOTALL)
_ENTITY_NBSP_RE: Final = re.compile(r"&nbsp;", re.IGNORECASE)
_ENTITY_AMP_RE: Final = re.compile(r"&amp;", re.IGNORECASE)
_ENTITY_LT_RE: Final = re.compile(r"&lt;", re.IGNORECASE)
_ENTITY_GT_RE: Final = re.compile(r"&gt;", re.IGNORECASE)
_ENTITY_QUOT_RE: Final = re.compile(r"&quot;", re.IGNORECASE)
_TITLE_RE: Final = re.compile(
    r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL
)


# ─── Limits / config ───────────────────────────────────────────────────────


WEB_USER_AGENT: Final = (
    "Mozilla/5.0 (compatible; NeuroMuleBot/1.0; +https://t.me/NeuroMule_bot)"
)
DEFAULT_HTTP_TIMEOUT_SEC: Final = 8.0
MAX_HTML_BYTES: Final = 3_000_000  # 3 MB — обрубаем огромные страницы.
DEFAULT_CLEAN_MAX_CHARS: Final = 8000


# ─── Helpers ───────────────────────────────────────────────────────────────


def find_urls(text: str) -> list[str]:
    """Возвращает все http/https URL из произвольного текста.

    Сохраняет порядок появления, удаляет дубликаты (стабильный set-by-order).
    """

    if not text:
        return []
    seen: dict[str, None] = {}
    for raw in _URL_RE.findall(text):
        url = raw.rstrip(".,;:!?)»'\"")
        seen.setdefault(url, None)
    return list(seen.keys())


def has_web_url(text: str) -> bool:
    """Быстрый детектор «в инпуте есть ссылка» (для роутинга на эксперт-режим)."""

    return bool(_URL_RE.search(text or ""))


def _strip_html(html: str) -> str:
    """Чистый текст из HTML: убираем head/script/style/комменты/теги/энтити."""

    if not html:
        return ""
    text = _HTML_COMMENT_RE.sub("", html)
    text = _HEAD_RE.sub("", text)
    text = _SCRIPT_STYLE_RE.sub("", text)
    text = _TAG_RE.sub("\n", text)
    text = _ENTITY_NBSP_RE.sub(" ", text)
    text = _ENTITY_AMP_RE.sub("&", text)
    text = _ENTITY_LT_RE.sub("<", text)
    text = _ENTITY_GT_RE.sub(">", text)
    text = _ENTITY_QUOT_RE.sub('"', text)
    return text


def _extract_title(html: str) -> str | None:
    if not html:
        return None
    match = _TITLE_RE.search(html)
    if not match:
        return None
    raw = compress_extracted_text(_strip_html(match.group(1)))
    return raw or None


async def fetch_and_clean(
    url: str,
    *,
    timeout: float = DEFAULT_HTTP_TIMEOUT_SEC,
    max_chars: int = DEFAULT_CLEAN_MAX_CHARS,
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Скачивает страницу по ``url`` и возвращает чистый текст.

    Args:
        url: полный http/https URL.
        timeout: жёсткий тайм-аут на запрос.
        max_chars: лимит длины результата для контекста ИИ.
        http_client: внешний клиент (для тестов / pooled coalition).

    Returns:
        ``str`` без HTML-тегов, прошедший :func:`compress_extracted_text`,
        обрезанный до ``max_chars`` (в самом конце добавлена ``[…]`` если
        обрезано). При любой ошибке сети / не-2xx / unsupported content-type
        → ``None``.
    """

    headers = {"User-Agent": WEB_USER_AGENT, "Accept": "text/html, */*"}
    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    try:
        resp = await client.get(url, headers=headers, timeout=timeout)
    except Exception:
        logger.info("web_browsing: GET failed for %s", url, exc_info=True)
        if own_client:
            await client.aclose()
        return None

    try:
        if resp.status_code != 200:
            logger.info("web_browsing: status %s for %s", resp.status_code, url)
            return None
        ctype = resp.headers.get("content-type", "").lower()
        if "html" not in ctype and "text" not in ctype:
            logger.info("web_browsing: skip non-html %s for %s", ctype, url)
            return None
        body = resp.content[:MAX_HTML_BYTES]
        try:
            html = body.decode(resp.encoding or "utf-8", errors="ignore")
        except LookupError:
            html = body.decode("utf-8", errors="ignore")
    finally:
        if own_client:
            await client.aclose()

    title = _extract_title(html)
    cleaned = compress_extracted_text(_strip_html(html))
    if not cleaned:
        return None

    suffix = ""
    if max_chars > 0 and len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars]
        suffix = "\n[…обрезано NeuroMule до лимита контекста]"

    header = f"[web] {title} — {url}\n" if title else f"[web] {url}\n"
    return header + cleaned + suffix


async def extract_browsing_context(
    text: str,
    *,
    max_urls: int = 2,
    max_chars_per_url: int = DEFAULT_CLEAN_MAX_CHARS,
    timeout: float = DEFAULT_HTTP_TIMEOUT_SEC,
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """High-level helper для чат-пайплайна.

    Находит до ``max_urls`` URL'ов в ``text``, fetch'ит их параллельно, чистит
    и склеивает в один блок. Возвращает ``None`` если URL'ов нет или все
    запросы упали.
    """

    urls = find_urls(text)[:max_urls]
    if not urls:
        return None

    import asyncio  # локально, чтобы не утяжелять верх модуля

    tasks = [
        fetch_and_clean(
            url,
            timeout=timeout,
            max_chars=max_chars_per_url,
            http_client=http_client,
        )
        for url in urls
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    chunks = [chunk for chunk in results if chunk]
    if not chunks:
        return None
    return "\n\n---\n\n".join(chunks)


__all__ = (
    "find_urls",
    "has_web_url",
    "fetch_and_clean",
    "extract_browsing_context",
    "WEB_USER_AGENT",
    "DEFAULT_HTTP_TIMEOUT_SEC",
    "DEFAULT_CLEAN_MAX_CHARS",
)
