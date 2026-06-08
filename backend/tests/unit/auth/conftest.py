"""Unit test configuration for auth.

This module provides fixtures for auth dependency tests,
including JWKS cache management and HTTP mocking.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.auth import dependencies as deps


@pytest.fixture(autouse=True)
def _reset_jwks_cache() -> Any:
    """Save and restore the module-level JWKS cache around each test."""
    old_keys = deps._jwks_cache["keys"]
    old_exp = deps._jwks_cache["expires_at"]
    deps._jwks_cache["keys"] = None
    deps._jwks_cache["expires_at"] = 0
    yield
    deps._jwks_cache["keys"] = old_keys
    deps._jwks_cache["expires_at"] = old_exp


@pytest.fixture
def mock_jwks_data() -> dict[str, list[dict[str, str]]]:
    """Sample JWKS data."""
    return {"keys": [{"kid": "kid-1", "kty": "RSA"}]}


@pytest.fixture
def mock_httpx(mock_jwks_data: dict[str, Any]) -> Any:
    """Patch httpx.AsyncClient so _load_jwks never makes real HTTP calls."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = mock_jwks_data
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    with patch.object(httpx, "AsyncClient", return_value=mock_client):
        yield mock_client
