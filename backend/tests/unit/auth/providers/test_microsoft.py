"""Unit tests for auth providers.

This module tests the Microsoft provider and the provider registry.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from jwt.exceptions import InvalidTokenError

from app.auth.providers import microsoft, registry
from app.auth.providers.microsoft import MicrosoftAuthProvider
from app.auth.providers.registry import get_provider
from app.auth.schemas import AuthProviderName


class TestGetProvider:
    """Resolve a provider name to its registered implementation."""

    def test_get_provider_returns_microsoft(self) -> None:
        """The registry resolves `microsoft` to the Microsoft provider."""
        assert isinstance(
            get_provider(AuthProviderName.MICROSOFT), MicrosoftAuthProvider
        )

    def test_get_provider_unknown_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unregistered provider name raises `KeyError`."""
        monkeypatch.setattr(registry, "_PROVIDERS", {})

        with pytest.raises(KeyError):
            get_provider(AuthProviderName.MICROSOFT)


class TestMicrosoftAuthProviderAuthenticate:
    """Validate a Microsoft id_token and normalize it to a `ProviderIdentity`."""

    async def test_authenticate_maps_all_claims(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Microsoft claims are mapped onto a `ProviderIdentity`, preserving oid/tid."""
        claims: dict[str, Any] = {
            "iss": "https://login.microsoftonline.com/tid/v2.0",
            "sub": "subject-1",
            "oid": "object-1",
            "tid": "tenant-1",
            "name": "Ada Lovelace",
            "preferred_username": "ada",
        }
        monkeypatch.setattr(
            microsoft, "validate_oidc_token", AsyncMock(return_value=claims)
        )

        identity = await MicrosoftAuthProvider().authenticate("id-token")

        assert identity.iss == claims["iss"]
        assert identity.sub == "subject-1"
        assert identity.oid == "object-1"
        assert identity.tid == "tenant-1"
        assert identity.name == "Ada Lovelace"
        assert identity.preferred_username == "ada"

    async def test_authenticate_optional_claims_default_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Absent optional claims become `None` on the identity."""
        claims: dict[str, Any] = {"iss": "issuer", "sub": "subject-1"}
        monkeypatch.setattr(
            microsoft, "validate_oidc_token", AsyncMock(return_value=claims)
        )

        identity = await MicrosoftAuthProvider().authenticate("id-token")

        assert identity.oid is None
        assert identity.tid is None
        assert identity.name is None
        assert identity.preferred_username is None

    @pytest.mark.parametrize(
        "claims",
        [
            pytest.param({"iss": "issuer"}, id="missing-sub"),
            pytest.param({"sub": "subject-1"}, id="missing-iss"),
            pytest.param({}, id="missing-both"),
            pytest.param({"iss": "issuer", "sub": ""}, id="empty-sub"),
            pytest.param({"iss": "", "sub": "subject-1"}, id="empty-iss"),
        ],
    )
    async def test_authenticate_missing_required_claim_raises(
        self, monkeypatch: pytest.MonkeyPatch, claims: dict[str, Any]
    ) -> None:
        """A token with a missing or empty `sub`/`iss` is rejected."""
        monkeypatch.setattr(
            microsoft, "validate_oidc_token", AsyncMock(return_value=claims)
        )

        with pytest.raises(InvalidTokenError, match="missing required"):
            await MicrosoftAuthProvider().authenticate("id-token")
