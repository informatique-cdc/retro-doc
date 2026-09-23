"""Unit tests for auth router.

This module tests the login, refresh and me HTTP endpoints.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from jwt.exceptions import InvalidTokenError

from app.auth import router
from app.auth.schemas import TokenResponse, User
from app.core.config import settings


def _token_response() -> TokenResponse:
    """Build a `TokenResponse` standing in for a freshly minted token pair."""
    return TokenResponse(access_token="acc", refresh_token="ref", expires_in=900)


class TestLoginEndpoint:
    """`POST /auth/login/{provider}` — exchange a provider credential for tokens."""

    async def test_login_success(
        self, monkeypatch: pytest.MonkeyPatch, mock_client: httpx.AsyncClient
    ) -> None:
        """A valid login returns 200 with the token pair."""
        monkeypatch.setattr(router, "login", AsyncMock(return_value=_token_response()))

        resp = await mock_client.post(
            "/auth/login/microsoft", json={"token": "id-token"}
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] == "acc"
        assert body["refresh_token"] == "ref"
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 900

    async def test_login_invalid_credentials_returns_401(
        self, monkeypatch: pytest.MonkeyPatch, mock_client: httpx.AsyncClient
    ) -> None:
        """An invalid provider credential maps to HTTP 401."""
        monkeypatch.setattr(
            router, "login", AsyncMock(side_effect=InvalidTokenError("bad"))
        )

        resp = await mock_client.post("/auth/login/microsoft", json={"token": "x"})

        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid authentication credentials"

    async def test_login_provider_unavailable_returns_503(
        self, monkeypatch: pytest.MonkeyPatch, mock_client: httpx.AsyncClient
    ) -> None:
        """A network failure reaching the provider maps to HTTP 503."""
        monkeypatch.setattr(
            router, "login", AsyncMock(side_effect=httpx.ConnectError("down"))
        )

        resp = await mock_client.post("/auth/login/microsoft", json={"token": "x"})

        assert resp.status_code == 503

    async def test_login_unknown_provider_returns_422(
        self, mock_client: httpx.AsyncClient
    ) -> None:
        """An unsupported provider in the path fails enum validation (422)."""
        resp = await mock_client.post("/auth/login/google", json={"token": "x"})

        assert resp.status_code == 422

    async def test_login_empty_token_returns_422(
        self, mock_client: httpx.AsyncClient
    ) -> None:
        """An empty credential fails request-body validation (422)."""
        resp = await mock_client.post("/auth/login/microsoft", json={"token": ""})

        assert resp.status_code == 422


class TestMeEndpoint:
    """`GET /auth/me` — echo the authenticated user's identity."""

    async def test_me_returns_current_user(
        self, mock_client: httpx.AsyncClient, user: User
    ) -> None:
        """`/me` echoes the authenticated user's identity."""
        from app.auth.dependencies import get_current_user
        from app.main import app

        app.dependency_overrides[get_current_user] = lambda: user
        try:
            resp = await mock_client.get("/auth/me")
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert body["uid"] == user.uid
        assert body["name"] == user.name
        assert body["preferred_username"] == user.preferred_username

    async def test_me_without_credentials_returns_401(
        self, mock_client: httpx.AsyncClient
    ) -> None:
        """A request with no bearer credentials is rejected by the scheme (401)."""
        resp = await mock_client.get("/auth/me")

        assert resp.status_code == 401

    async def test_me_with_invalid_token_returns_401(
        self, monkeypatch: pytest.MonkeyPatch, mock_client: httpx.AsyncClient
    ) -> None:
        """A malformed bearer token is rejected during verification (401)."""
        monkeypatch.setattr(settings, "APP_DEBUG", False)

        resp = await mock_client.get(
            "/auth/me", headers={"Authorization": "Bearer garbage"}
        )

        assert resp.status_code == 401


class TestRefreshEndpoint:
    """`POST /auth/refresh` — rotate a refresh token into a fresh token pair."""

    async def test_refresh_success(
        self, monkeypatch: pytest.MonkeyPatch, mock_client: httpx.AsyncClient
    ) -> None:
        """A valid refresh returns 200 with a fresh token pair."""
        monkeypatch.setattr(
            router, "refresh", MagicMock(return_value=_token_response())
        )

        resp = await mock_client.post("/auth/refresh", json={"refresh_token": "ref"})

        assert resp.status_code == 200
        assert resp.json()["access_token"] == "acc"

    async def test_refresh_invalid_returns_401(
        self, monkeypatch: pytest.MonkeyPatch, mock_client: httpx.AsyncClient
    ) -> None:
        """An invalid refresh token maps to HTTP 401."""
        monkeypatch.setattr(
            router, "refresh", MagicMock(side_effect=InvalidTokenError("bad"))
        )

        resp = await mock_client.post("/auth/refresh", json={"refresh_token": "x"})

        assert resp.status_code == 401

    async def test_refresh_empty_returns_422(
        self, mock_client: httpx.AsyncClient
    ) -> None:
        """An empty refresh token fails request-body validation (422)."""
        resp = await mock_client.post("/auth/refresh", json={"refresh_token": ""})

        assert resp.status_code == 422
