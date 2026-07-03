"""Unit tests for auth dependencies.

`get_current_user` now verifies an app-issued access token (HS256) instead of
validating an external provider token on every request.
"""

from typing import Any
from unittest.mock import patch

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
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# ---------------------------------------------------------------------------
# get_current_user — debug short-circuit
# ---------------------------------------------------------------------------


async def test_debug_mode_returns_debug_user() -> None:
    """Both debug flags `True` uses `create_debug_payload` and skips decoding."""
    with (
        patch.object(settings, "APP_DEBUG", True),
        patch.object(auth_settings, "APP_AUTH_DEBUG", True),
        patch.object(deps, "decode_internal_token") as mock_decode,
    ):
        user = await get_current_user(_make_credentials())

    mock_decode.assert_not_called()
    assert user.name == "John Doe"
    assert user.uid is not None


async def test_debug_requires_both_flags(payload: dict[str, Any]) -> None:
    """`APP_DEBUG=True` and `APP_AUTH_DEBUG=False` still decodes the token."""
    with (
        patch.object(settings, "APP_DEBUG", True),
        patch.object(auth_settings, "APP_AUTH_DEBUG", False),
        patch.object(
            deps, "decode_internal_token", return_value=payload
        ) as mock_decode,
    ):
        await get_current_user(_make_credentials())

    mock_decode.assert_called_once()


# ---------------------------------------------------------------------------
# get_current_user — real token verification
# ---------------------------------------------------------------------------


async def test_accepts_valid_access_token(token_claims: TokenClaims) -> None:
    """A genuine access token is accepted and its claims rebuild the user."""
    token = create_access_token(token_claims)

    with patch.object(settings, "APP_DEBUG", False):
        user = await get_current_user(_make_credentials(token))

    assert user.uid == token_claims.uid
    assert user.name == token_claims.name


async def test_rejects_refresh_token(token_claims: TokenClaims) -> None:
    """A refresh token is rejected by the access-only dependency (401)."""
    token = create_refresh_token(token_claims)

    with (
        patch.object(settings, "APP_DEBUG", False),
        pytest.raises(HTTPException) as exc_info,
    ):
        await get_current_user(_make_credentials(token))

    assert exc_info.value.status_code == 401


async def test_rejects_expired_access_token(token_claims: TokenClaims) -> None:
    """An expired access token produces HTTP 401."""
    with patch.object(auth_settings, "JWT_ACCESS_TOKEN_DURATION_S", -10):
        token = create_access_token(token_claims)

    with (
        patch.object(settings, "APP_DEBUG", False),
        pytest.raises(HTTPException) as exc_info,
    ):
        await get_current_user(_make_credentials(token))

    assert exc_info.value.status_code == 401


async def test_raises_401_on_decode_failure() -> None:
    """`InvalidTokenError` while decoding produces a uniform HTTP 401."""
    with (
        patch.object(settings, "APP_DEBUG", False),
        patch.object(
            deps,
            "decode_internal_token",
            side_effect=InvalidTokenError("bad"),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await get_current_user(_make_credentials())

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid authentication credentials"
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}
