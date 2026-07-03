"""Unit tests for the Microsoft provider and the provider registry."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from jwt.exceptions import InvalidTokenError

from app.auth.providers.microsoft import MicrosoftAuthProvider
from app.auth.providers.registry import get_provider
from app.auth.schemas import AuthProviderName

_VALIDATE = "app.auth.providers.microsoft.validate_oidc_token"


# ---------------------------------------------------------------------------
# MicrosoftAuthProvider.authenticate
# ---------------------------------------------------------------------------


async def test_authenticate_maps_all_claims() -> None:
    """Microsoft claims are mapped onto a `ProviderIdentity`, preserving oid/tid."""
    claims: dict[str, Any] = {
        "iss": "https://login.microsoftonline.com/tid/v2.0",
        "sub": "subject-1",
        "oid": "object-1",
        "tid": "tenant-1",
        "name": "Ada Lovelace",
        "preferred_username": "ada",
    }

    with patch(_VALIDATE, new_callable=AsyncMock, return_value=claims):
        identity = await MicrosoftAuthProvider().authenticate("id-token")

    assert identity.iss == claims["iss"]
    assert identity.sub == "subject-1"
    assert identity.oid == "object-1"
    assert identity.tid == "tenant-1"
    assert identity.name == "Ada Lovelace"
    assert identity.preferred_username == "ada"


async def test_authenticate_optional_claims_default_none() -> None:
    """Absent optional claims become `None` on the identity."""
    claims: dict[str, Any] = {"iss": "issuer", "sub": "subject-1"}

    with patch(_VALIDATE, new_callable=AsyncMock, return_value=claims):
        identity = await MicrosoftAuthProvider().authenticate("id-token")

    assert identity.oid is None
    assert identity.tid is None
    assert identity.name is None
    assert identity.preferred_username is None


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "issuer"},
        {"sub": "subject-1"},
        {},
        {"iss": "issuer", "sub": ""},
        {"iss": "", "sub": "subject-1"},
    ],
    ids=["missing_sub", "missing_iss", "missing_both", "empty_sub", "empty_iss"],
)
async def test_authenticate_missing_required_claim_raises(
    claims: dict[str, Any],
) -> None:
    """A token with a missing or empty `sub`/`iss` is rejected."""
    with (
        patch(_VALIDATE, new_callable=AsyncMock, return_value=claims),
        pytest.raises(InvalidTokenError, match="missing required"),
    ):
        await MicrosoftAuthProvider().authenticate("id-token")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_get_provider_returns_microsoft() -> None:
    """The registry resolves `microsoft` to the Microsoft provider."""
    assert isinstance(get_provider(AuthProviderName.MICROSOFT), MicrosoftAuthProvider)


def test_get_provider_unknown_raises() -> None:
    """An unregistered provider name raises `KeyError`."""
    with (
        patch.dict("app.auth.providers.registry._PROVIDERS", {}, clear=True),
        pytest.raises(KeyError),
    ):
        get_provider(AuthProviderName.MICROSOFT)
