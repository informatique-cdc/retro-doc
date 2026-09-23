"""Unit tests for auth dependencies.

`get_current_user` now verifies an app-issued access token (HS256) instead of
validating an external provider token on every request.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jwt.exceptions import InvalidTokenError

from app.auth import dependencies as deps
from app.auth.config import auth_settings
from app.auth.dependencies import get_current_user
from app.auth.schemas import TokenClaims
from app.auth.tokens import create_access_token, create_refresh_token
from app.core.config import settings


def _make_credentials(token: str = "fake-token") -> HTTPAuthorizationCredentials:
    """Build bearer `HTTPAuthorizationCredentials` carrying `token`."""
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


class TestGetCurrentUser:
    """Resolve the authenticated user from an app-issued access token."""

    async def test_debug_mode_returns_debug_user(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both debug flags `True` uses `_create_debug_payload` and skips decoding."""
        mock_decode = MagicMock()
        monkeypatch.setattr(settings, "APP_DEBUG", True)
        monkeypatch.setattr(auth_settings, "APP_AUTH_DEBUG", True)
        monkeypatch.setattr(deps, "decode_internal_token", mock_decode)

        user = await get_current_user(_make_credentials())

        mock_decode.assert_not_called()
        assert user.name == "John Doe"
        assert user.uid is not None

    async def test_debug_requires_both_flags(
        self, monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
    ) -> None:
        """`APP_DEBUG=True` and `APP_AUTH_DEBUG=False` still decodes the token."""
        mock_decode = MagicMock(return_value=payload)
        monkeypatch.setattr(settings, "APP_DEBUG", True)
        monkeypatch.setattr(auth_settings, "APP_AUTH_DEBUG", False)
        monkeypatch.setattr(deps, "decode_internal_token", mock_decode)

        await get_current_user(_make_credentials())

        mock_decode.assert_called_once()

    async def test_accepts_valid_access_token(
        self, monkeypatch: pytest.MonkeyPatch, token_claims: TokenClaims
    ) -> None:
        """A genuine access token is accepted and its claims rebuild the user."""
        token = create_access_token(token_claims)
        monkeypatch.setattr(settings, "APP_DEBUG", False)

        user = await get_current_user(_make_credentials(token))

        assert user.uid == token_claims.uid
        assert user.name == token_claims.name

    async def test_rejects_refresh_token(
        self, monkeypatch: pytest.MonkeyPatch, token_claims: TokenClaims
    ) -> None:
        """A refresh token is rejected by the access-only dependency (401)."""
        token = create_refresh_token(token_claims)
        monkeypatch.setattr(settings, "APP_DEBUG", False)

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(_make_credentials(token))

        assert exc_info.value.status_code == 401

    async def test_rejects_expired_access_token(
        self, monkeypatch: pytest.MonkeyPatch, token_claims: TokenClaims
    ) -> None:
        """An expired access token produces HTTP 401."""
        monkeypatch.setattr(auth_settings, "JWT_ACCESS_TOKEN_DURATION_S", -10)
        token = create_access_token(token_claims)
        monkeypatch.setattr(settings, "APP_DEBUG", False)

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(_make_credentials(token))

        assert exc_info.value.status_code == 401

    async def test_raises_401_on_decode_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`InvalidTokenError` while decoding produces a uniform HTTP 401."""
        mock_decode = MagicMock(side_effect=InvalidTokenError("bad"))
        monkeypatch.setattr(settings, "APP_DEBUG", False)
        monkeypatch.setattr(deps, "decode_internal_token", mock_decode)

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(_make_credentials())

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid authentication credentials"
        assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}
