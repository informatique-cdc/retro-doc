"""Unit tests for the auth service (login / refresh orchestration)."""

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jwt.exceptions import InvalidTokenError

from app.auth.config import auth_settings
from app.auth.providers.base import ProviderIdentity
from app.auth.schemas import AuthProviderName, TokenClaims
from app.auth.service import login, refresh
from app.auth.tokens import (
    ACCESS_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_internal_token,
)
from app.auth.utils import compute_uid

_GET_PROVIDER = "app.auth.service.get_provider"


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


async def test_login_returns_token_pair_with_stable_uid() -> None:
    """A successful login issues access + refresh tokens carrying a stable uid."""
    identity = ProviderIdentity(
        iss="https://login.microsoftonline.com/t/v2.0",
        sub="subject-1",
        oid="object-1",
        tid="tenant-1",
        name="Ada",
        preferred_username="ada",
    )
    provider = MagicMock()
    provider.authenticate = AsyncMock(return_value=identity)

    with patch(_GET_PROVIDER, return_value=provider):
        result = await login(AuthProviderName.MICROSOFT, "id-token")

    provider.authenticate.assert_awaited_once_with("id-token")
    assert result.token_type == "bearer"
    assert result.expires_in == auth_settings.JWT_ACCESS_TOKEN_DURATION_S

    expected_uid = compute_uid(
        oid="object-1", tid="tenant-1", iss=identity.iss, sub="subject-1"
    )
    access = decode_internal_token(result.access_token, expected_type=ACCESS_TOKEN_TYPE)
    refresh_payload = decode_internal_token(
        result.refresh_token, expected_type=REFRESH_TOKEN_TYPE
    )
    assert access["uid"] == expected_uid
    assert refresh_payload["uid"] == expected_uid


async def test_login_unknown_provider_propagates() -> None:
    """An unregistered provider surfaces as `KeyError` for the router to map."""
    with (
        patch(_GET_PROVIDER, side_effect=KeyError("nope")),
        pytest.raises(KeyError),
    ):
        await login(AuthProviderName.MICROSOFT, "id-token")


async def test_login_invalid_credential_propagates() -> None:
    """An invalid provider credential surfaces as `InvalidTokenError`."""
    provider = MagicMock()
    provider.authenticate = AsyncMock(side_effect=InvalidTokenError("bad"))

    with (
        patch(_GET_PROVIDER, return_value=provider),
        pytest.raises(InvalidTokenError),
    ):
        await login(AuthProviderName.MICROSOFT, "bad-token")


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


def test_refresh_issues_new_tokens(token_claims: TokenClaims) -> None:
    """A valid refresh token yields a fresh pair preserving the identity."""
    result = refresh(create_refresh_token(token_claims))

    assert result.token_type == "bearer"
    access = decode_internal_token(result.access_token, expected_type=ACCESS_TOKEN_TYPE)
    assert access["uid"] == token_claims.uid
    assert access["sub"] == token_claims.sub
    assert access["oid"] == token_claims.oid


def _expired_refresh_token(token_claims: TokenClaims) -> str:
    """A refresh token already past its expiry."""
    with patch.object(auth_settings, "JWT_REFRESH_TOKEN_DURATION_S", -10):
        return create_refresh_token(token_claims)


@pytest.mark.parametrize(
    "make_token",
    [
        create_access_token,
        _expired_refresh_token,
        lambda _claims: "not-a-jwt",
    ],
    ids=["access_token", "expired", "garbage"],
)
def test_refresh_rejects(
    token_claims: TokenClaims, make_token: Callable[[TokenClaims], str]
) -> None:
    """`refresh` rejects anything but a valid refresh token: an access token, an
    expired refresh token, or a malformed string."""
    with pytest.raises(InvalidTokenError):
        refresh(make_token(token_claims))
