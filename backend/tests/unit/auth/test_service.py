"""Unit tests for auth service.

This module tests the login and refresh orchestration.
"""

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from jwt.exceptions import InvalidTokenError

from app.auth import service
from app.auth.config import auth_settings
from app.auth.identity import compute_uid
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


def _expired_refresh_token(token_claims: TokenClaims) -> str:
    """A refresh token already past its expiry."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(auth_settings, "JWT_REFRESH_TOKEN_DURATION_S", -10)
        return create_refresh_token(token_claims)


class TestLogin:
    """Exchange a provider credential for an app-issued token pair."""

    async def test_login_returns_token_pair_with_stable_uid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
        monkeypatch.setattr(service, "get_provider", MagicMock(return_value=provider))

        result = await login(
            AuthProviderName.MICROSOFT,
            "id-token",  # betterleaks:allow
        )

        provider.authenticate.assert_awaited_once_with("id-token")
        assert result.token_type == "bearer"
        assert result.expires_in == auth_settings.JWT_ACCESS_TOKEN_DURATION_S

        expected_uid = compute_uid(
            oid="object-1", tid="tenant-1", iss=identity.iss, sub="subject-1"
        )
        access = decode_internal_token(
            result.access_token, expected_type=ACCESS_TOKEN_TYPE
        )
        refresh_payload = decode_internal_token(
            result.refresh_token, expected_type=REFRESH_TOKEN_TYPE
        )
        assert access["uid"] == expected_uid
        assert refresh_payload["uid"] == expected_uid

    async def test_login_unknown_provider_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unregistered provider surfaces as `KeyError` for the router to map."""
        monkeypatch.setattr(
            service, "get_provider", MagicMock(side_effect=KeyError("nope"))
        )

        with pytest.raises(KeyError):
            await login(AuthProviderName.MICROSOFT, "id-token")  # betterleaks:allow

    async def test_login_invalid_credential_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An invalid provider credential surfaces as `InvalidTokenError`."""
        provider = MagicMock()
        provider.authenticate = AsyncMock(side_effect=InvalidTokenError("bad"))
        monkeypatch.setattr(service, "get_provider", MagicMock(return_value=provider))

        with pytest.raises(InvalidTokenError):
            await login(AuthProviderName.MICROSOFT, "bad-token")  # betterleaks:allow


class TestRefresh:
    """Issue a fresh token pair from a valid refresh token."""

    def test_refresh_issues_new_tokens(self, token_claims: TokenClaims) -> None:
        """A valid refresh token yields a fresh pair preserving the identity."""
        result = refresh(create_refresh_token(token_claims))

        assert result.token_type == "bearer"
        access = decode_internal_token(
            result.access_token, expected_type=ACCESS_TOKEN_TYPE
        )
        assert access["uid"] == token_claims.uid
        assert access["sub"] == token_claims.sub
        assert access["oid"] == token_claims.oid

    @pytest.mark.parametrize(
        "make_token",
        [
            pytest.param(create_access_token, id="access-token"),
            pytest.param(_expired_refresh_token, id="expired"),
            pytest.param(lambda _claims: "not-a-jwt", id="garbage"),
        ],
    )
    def test_refresh_rejects(
        self, token_claims: TokenClaims, make_token: Callable[[TokenClaims], str]
    ) -> None:
        """`refresh` rejects anything but a valid refresh token."""
        with pytest.raises(InvalidTokenError):
            refresh(make_token(token_claims))
