"""Unit test configuration for auth.

This module provides fixtures for auth provider tests,
including JWKS cache management and HTTP mocking.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.auth import oidc
from app.auth.schemas import TokenClaims


@pytest.fixture(autouse=True)
def _reset_jwks_cache() -> Any:
    """Save and restore the module-level JWKS cache around each test."""
    old_keys = oidc._jwks_cache["keys"]
    old_exp = oidc._jwks_cache["expires_at"]
    oidc._jwks_cache["keys"] = None
    oidc._jwks_cache["expires_at"] = 0
    yield
    oidc._jwks_cache["keys"] = old_keys
    oidc._jwks_cache["expires_at"] = old_exp


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


@pytest.fixture
def token_claims() -> TokenClaims:
    """Identity claims for issuing app tokens, matching the `payload` fixture."""
    return TokenClaims(
        uid="stable-user-uid",
        sub="test-subject-id",
        name="Test User",
        preferred_username="testuser",
        oid="00000000-0000-0000-0000-000000000001",
        tid="00000000-0000-0000-0000-000000000002",
    )
