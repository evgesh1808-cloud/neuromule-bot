"""Минимальный backend для будущего Mini App (Telegram / MAX)."""
from fastapi import FastAPI

app = FastAPI(title="NeuroMule Mini App API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "NeuroMule", "hint": "Подключите фронтенд (React/Vue) и Telegram Web App SDK."}
