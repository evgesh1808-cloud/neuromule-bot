"""OpenRouter probe при старте не должен блокировать polling."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from config import Settings


@pytest.mark.asyncio
async def test_wait_openrouter_api_continues_after_network_failure() -> None:
    from services.openrouter_http import _wait_openrouter_api

    settings = Settings(tg_token="t", openrouter_key="test-key")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("blocked"))

    with patch(
        "services.billing.chat_pipeline.resolve_openrouter_api_key",
        return_value="test-key",
    ), patch(
        "services.openrouter_http.get_openrouter_http_client",
        AsyncMock(return_value=mock_client),
    ):
        await _wait_openrouter_api(settings)

    assert mock_client.get.await_count == 5
