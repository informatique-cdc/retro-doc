"""Unit tests for healthz router.

This module tests the healthz router.
"""

import httpx


async def test_healthz_status(mock_client: httpx.AsyncClient) -> None:
    response = await mock_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "up"}
