"""Unit tests for healthz router.

This module tests the healthz router.
"""

import httpx


class TestHealthzEndpoint:
    """`GET /healthz` — report whether the service is up."""

    async def test_healthz_status(self, mock_client: httpx.AsyncClient) -> None:
        """GET /healthz reports the service as up."""
        response = await mock_client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "up"}
