"""Unit tests for the languages router.

This module tests the languages router using `httpx.AsyncClient` with the
worker fetch mocked and authentication overridden.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

pytestmark = pytest.mark.usefixtures("_override_deps")


async def test_get_languages_returns_supported(mock_client: httpx.AsyncClient) -> None:
    """GET /languages returns the worker's supported languages."""
    with patch(
        "app.languages.router.get_supported_languages",
        new_callable=AsyncMock,
        return_value=["java"],
    ):
        resp = await mock_client.get("/languages")

    assert resp.status_code == 200
    assert resp.json() == {"languages": ["java"]}
