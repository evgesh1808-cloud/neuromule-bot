"""GET /api/v1/hd/report — Mini App HD contract."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import sign_init_data_for_tests
from api.mini_app import app
from config import settings


@pytest.mark.asyncio
async def test_hd_report_requires_auth() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/hd/report")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_hd_report_not_purchased_404() -> None:
    token = (settings.tg_token or "test:token").strip()
    init_data = sign_init_data_for_tests(token, user_id=700001)
    fake_row = type(
        "Row",
        (),
        {
            "has_pro_analysis": False,
            "hd_report_json": None,
            "hd_type": "",
            "hd_birth_data": "",
        },
    )()
    with patch("api.hd_endpoints.get_user_row", new=AsyncMock(return_value=fake_row)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/hd/report",
                headers={"Authorization": f"tma {init_data}"},
            )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_hd_report_success_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_BASE_URL", "https://api.example.com")
    token = (settings.tg_token or "test:token").strip()
    init_data = sign_init_data_for_tests(token, user_id=700002)
    report_json = json.dumps(
        {
            "fast_facts": "⚡ Баг: спешка. 💼 Деньги: отклик. 🔋 Сон.",
            "money": "### Боль\nТекст",
            "love": "Love",
            "energy": "Energy",
            "plan": "Plan",
            "energy_scales": {"capacity": 70, "immunity": 60, "scale": 75},
        },
        ensure_ascii=False,
    )
    fake_row = type(
        "Row",
        (),
        {
            "has_pro_analysis": True,
            "hd_report_json": report_json,
            "hd_type": "Генератор",
            "hd_birth_data": "15.03.1990 14:30 Москва",
        },
    )()
    with (
        patch("api.hd_endpoints.get_user_row", new=AsyncMock(return_value=fake_row)),
        patch("api.hd_endpoints.generate_premium_bodygraph", return_value="tmp/ready_hd_700002.png"),
        patch("api.hd_endpoints.build_hd_math_data") as math_mock,
        patch("api.hd_endpoints.hd_profile_metadata") as meta_mock,
    ):
        math_mock.return_value = {"hd_type": "Генератор", "birth_data": "15.03.1990 14:30 Москва"}
        meta_mock.return_value = {
            "hd_type": "Генератор",
            "birth_data": "15.03.1990 14:30 Москва",
            "defined_centers": ["Сакрал"],
            "open_centers": ["Голова"],
            "strategy": "",
            "authority": "",
            "profile": "3/5",
        }
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/hd/report",
                headers={"Authorization": f"tma {init_data}"},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_pro_analysis"] is True
    assert body["hd_type"] == "Генератор"
    assert "fast_facts" in body["report"]
    assert body["bodygraph_url"] == "https://api.example.com/media/hd/ready_hd_700002.png"
    assert body["defined_centers"] == ["Сакрал"]
    assert body["profile"] == "3/5"
    assert body["energy_scales"]["capacity"] == 70
