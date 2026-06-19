"""Тесты Web Browsing (URL-детектор + fetch HTML + чистый текст)."""

from __future__ import annotations

import httpx
import pytest

from services.web_browsing import (
    DEFAULT_CLEAN_MAX_CHARS,
    WEB_USER_AGENT,
    extract_browsing_context,
    fetch_and_clean,
    find_urls,
    has_web_url,
)


# ─── find_urls / has_web_url ───────────────────────────────────────────────


def test_find_urls_extracts_http_and_https() -> None:
    text = "посмотри https://example.com/a и http://x.io/page1 — потом https://example.com/a"
    urls = find_urls(text)
    assert urls == ["https://example.com/a", "http://x.io/page1"]  # dedup, порядок


def test_find_urls_strips_trailing_punctuation() -> None:
    text = "ссылка: https://example.com/article!"
    assert find_urls(text) == ["https://example.com/article"]


def test_find_urls_returns_empty_for_no_urls() -> None:
    assert find_urls("") == []
    assert find_urls("просто текст без линков") == []


def test_has_web_url_detects_http_in_message() -> None:
    assert has_web_url("читай тут https://t.me/blog") is True
    assert has_web_url("ничего важного") is False


# ─── fetch_and_clean (httpx MockTransport) ────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_and_clean_strips_html_and_extracts_title() -> None:
    html = """
    <!doctype html>
    <html>
      <head>
        <title>Тестовая статья NeuroMule</title>
        <style>body{color:red}</style>
        <script>alert('hi')</script>
      </head>
      <body>
        <h1>Заголовок</h1>
        <p>Первый параграф со <b>смыслом</b>.</p>
        <p>Второй параграф.</p>
        <!-- комментарий -->
      </body>
    </html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=html.encode("utf-8"),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_and_clean(
            "https://example.com/article",
            http_client=client,
        )

    assert result is not None
    assert "Тестовая статья NeuroMule" in result
    assert "Заголовок" in result
    assert "Первый параграф" in result
    assert "Второй параграф" in result
    # HTML/JS должны исчезнуть
    assert "<script>" not in result
    assert "alert" not in result
    assert "color:red" not in result
    assert "[web]" in result


@pytest.mark.asyncio
async def test_fetch_and_clean_returns_none_for_non_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"<html>nope</html>")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_and_clean(
            "https://example.com/missing",
            http_client=client,
        )

    assert result is None


@pytest.mark.asyncio
async def test_fetch_and_clean_skips_non_html_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"ok": true}',
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_and_clean(
            "https://api.example.com/data.json",
            http_client=client,
        )

    assert result is None


@pytest.mark.asyncio
async def test_fetch_and_clean_truncates_to_max_chars() -> None:
    body_text = "abc " * 5000  # ~20 000 символов
    html = f"<html><body><p>{body_text}</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=html.encode("utf-8"),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_and_clean(
            "https://example.com/long",
            max_chars=500,
            http_client=client,
        )

    assert result is not None
    assert "[…обрезано NeuroMule до лимита контекста]" in result


@pytest.mark.asyncio
async def test_fetch_and_clean_network_error_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_and_clean(
            "https://example.com",
            http_client=client,
        )

    assert result is None


# ─── extract_browsing_context (high-level) ─────────────────────────────────


@pytest.mark.asyncio
async def test_extract_browsing_context_combines_multiple_urls() -> None:
    pages = {
        "https://a.example.com/": "<html><title>A</title><body>Alpha contents</body></html>",
        "https://b.example.com/": "<html><title>B</title><body>Beta contents</body></html>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(str(request.url), "<html></html>")
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=body.encode("utf-8"),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        joined = await extract_browsing_context(
            "посмотри https://a.example.com/ и https://b.example.com/",
            max_urls=2,
            http_client=client,
        )

    assert joined is not None
    assert "Alpha contents" in joined
    assert "Beta contents" in joined
    assert "---" in joined  # разделитель между ресурсами


@pytest.mark.asyncio
async def test_extract_browsing_context_returns_none_when_no_urls() -> None:
    out = await extract_browsing_context("без ссылок текст")
    assert out is None


def test_default_max_chars_is_sane() -> None:
    assert 1000 <= DEFAULT_CLEAN_MAX_CHARS <= 50_000


def test_web_user_agent_brand() -> None:
    assert "NeuroMule" in WEB_USER_AGENT
