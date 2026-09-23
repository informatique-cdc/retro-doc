"""Unit tests for the languages router.

This module tests the languages router using `httpx.AsyncClient` with the
worker fetch mocked and authentication overridden.
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.auth.dependencies import get_current_user
from app.auth.schemas import User
from app.languages import router

pytestmark = pytest.mark.usefixtures("_override_deps")


@pytest.fixture
def _override_deps(monkeypatch: pytest.MonkeyPatch, user: User) -> None:
    """Override the FastAPI auth dependency for languages HTTP tests."""
    from app.main import app

    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: user)


class TestGetLanguagesEndpoint:
    """`GET /languages` — expose the worker's supported language set."""

    async def test_get_languages_returns_supported(
        self, monkeypatch: pytest.MonkeyPatch, mock_client: httpx.AsyncClient
    ) -> None:
        """GET /languages returns the worker's supported languages."""
        monkeypatch.setattr(
            router, "get_supported_languages", AsyncMock(return_value=["java"])
        )

        resp = await mock_client.get("/languages")

        assert resp.status_code == 200
        assert resp.json() == {"languages": ["java"]}
