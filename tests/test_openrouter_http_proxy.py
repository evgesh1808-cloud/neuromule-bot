"""Прокси OpenRouter: AI_PROXY → httpx.AsyncClient kwargs."""

from __future__ import annotations

from config import Settings
from services.openrouter_http import openrouter_client_kwargs, resolve_ai_proxy_url


def test_resolve_ai_proxy_empty():
    s = Settings().model_copy(update={"ai_proxy": None})
    assert resolve_ai_proxy_url(s) is None
    assert openrouter_client_kwargs(s) == {"trust_env": False}


def test_resolve_ai_proxy_blank_string():
    s = Settings().model_copy(update={"ai_proxy": "   "})
    assert resolve_ai_proxy_url(s) is None


def test_openrouter_client_kwargs_sets_proxy():
    s = Settings().model_copy(update={"ai_proxy": "socks5://10.0.0.1:1080"})
    assert resolve_ai_proxy_url(s) == "socks5://10.0.0.1:1080"
    kw = openrouter_client_kwargs(s, timeout=30.0)
    assert kw == {
        "timeout": 30.0,
        "proxy": "socks5://10.0.0.1:1080",
        "trust_env": False,
    }


def test_openrouter_client_kwargs_normalizes_host_port():
    s = Settings().model_copy(update={"ai_proxy": "127.0.0.1:7890"})
    assert resolve_ai_proxy_url(s) == "http://127.0.0.1:7890"
    assert openrouter_client_kwargs(s)["proxy"] == "http://127.0.0.1:7890"
    assert openrouter_client_kwargs(s)["trust_env"] is False
