"""Разрешение HTTP/SOCKS-прокси для подключения aiogram к Telegram API."""
from __future__ import annotations

import logging
import os
import socket
import sys

logger = logging.getLogger(__name__)

# Порты локальных VPN-клиентов (Clash, V2Ray, Hiddify и т.п.) — если в .env пусто.
_LOCAL_PROXY_PORTS: tuple[int, ...] = (7890, 7891, 10808, 10809, 1080, 8080, 8118)
_LOCAL_PROXY_PROBE_TIMEOUT_SEC = 0.2


def _normalize_proxy_url(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if "://" not in value:
        return f"http://{value}"
    return value


def _windows_system_proxy() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not int(enabled):
                return ""
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return ""
    server = str(server or "").strip()
    if not server:
        return ""
    if ";" in server:
        for part in server.split(";"):
            part = part.strip()
            if part.lower().startswith("https="):
                return _normalize_proxy_url(part.split("=", 1)[1])
        for part in server.split(";"):
            part = part.strip()
            if part.lower().startswith("http="):
                return _normalize_proxy_url(part.split("=", 1)[1])
        return ""
    return _normalize_proxy_url(server)


def _probe_local_http_proxy() -> str | None:
    """Если VPN поднял локальный HTTP-прокси, но в .env строки нет — подхватить порт."""
    for port in _LOCAL_PROXY_PORTS:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=_LOCAL_PROXY_PROBE_TIMEOUT_SEC):
                return f"http://127.0.0.1:{port}"
        except OSError:
            continue
    return None


def resolve_telegram_proxy_url(explicit: str | None = None) -> str | None:
    """Прокси для Bot session: .env → env → системный прокси Windows → локальный порт VPN."""
    candidates: list[tuple[str, str]] = [
        ("TELEGRAM_PROXY_URL (.env)", (explicit or "").strip()),
        ("TELEGRAM_PROXY_URL (env)", os.environ.get("TELEGRAM_PROXY_URL", "").strip()),
        ("HTTPS_PROXY", os.environ.get("HTTPS_PROXY", "").strip()),
        ("https_proxy", os.environ.get("https_proxy", "").strip()),
        ("HTTP_PROXY", os.environ.get("HTTP_PROXY", "").strip()),
        ("http_proxy", os.environ.get("http_proxy", "").strip()),
        ("Windows system proxy", _windows_system_proxy()),
    ]
    for source, raw in candidates:
        if not raw:
            continue
        url = _normalize_proxy_url(raw)
        logger.info("Telegram proxy: %s → %s", source, _redact_proxy_url(url))
        return url
    auto = _probe_local_http_proxy()
    if auto:
        logger.info(
            "Telegram proxy: local auto-detect → %s (добавьте TELEGRAM_PROXY_URL в .env для явного порта)",
            _redact_proxy_url(auto),
        )
        return auto
    return None


def _redact_proxy_url(url: str) -> str:
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    if "@" in rest:
        _, host = rest.rsplit("@", 1)
        return f"{scheme}://***@{host}"
    return url
