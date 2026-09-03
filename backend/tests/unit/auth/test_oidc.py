"""Unit tests for generic OIDC validation (`app.auth.oidc`).

Covers the JWKS cache (`_load_jwks`) and token validation
(`validate_oidc_token`), including the key-rotation retry, the non-RSA guard,
and — via a real RSA keypair — the now-enforced expiry check.
"""

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from jwt.algorithms import RSAAlgorithm
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

from app.auth import oidc
from app.auth.config import auth_settings
from app.auth.oidc import _load_jwks, validate_oidc_token


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[RSAPrivateKey, dict[str, Any]]:
    """A real RSA private key and its matching JWK (with a `kid`)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk["kid"] = "test-kid"
    return private_key, jwk


def _sign(private_key: RSAPrivateKey, kid: str, **overrides: Any) -> str:
    """Sign an RS256 OIDC token valid for the configured audience/issuer."""
    claims: dict[str, Any] = {
        "iss": auth_settings.OIDC_ALLOWED_ISSUERS[0],
        "aud": auth_settings.OIDC_AUDIENCE,
        "sub": "subject-1",
        "exp": int(time.time()) + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


# ---------------------------------------------------------------------------
# _load_jwks
# ---------------------------------------------------------------------------


async def test_load_jwks_double_check_inside_lock(
    mock_httpx: Any, mock_jwks_data: Any
) -> None:
    """A caller that enters the lock after the cache was populated returns
    early without an HTTP call (double-check pattern)."""
    async with oidc._jwks_lock:
        task = asyncio.create_task(_load_jwks())
        await asyncio.sleep(0.05)
        oidc._jwks_cache["keys"] = mock_jwks_data
        oidc._jwks_cache["expires_at"] = time.time() + 9999

    result = await task

    assert result == mock_jwks_data
    mock_httpx.get.assert_not_awaited()


async def test_load_jwks_fetches_when_cache_empty(
    mock_httpx: Any, mock_jwks_data: Any
) -> None:
    """An empty cache triggers an HTTP fetch and populates the cache."""
    result = await _load_jwks()

    assert result == mock_jwks_data
    mock_httpx.get.assert_awaited_once()
    assert oidc._jwks_cache["keys"] == mock_jwks_data


async def test_load_jwks_force_bypasses_cache(
    mock_httpx: Any, mock_jwks_data: Any
) -> None:
    """`force=True` fetches even when the cache is still valid."""
    oidc._jwks_cache["keys"] = {"keys": [{"kid": "old"}]}
    oidc._jwks_cache["expires_at"] = time.time() + 9999

    result = await _load_jwks(force=True)

    assert result == mock_jwks_data
    mock_httpx.get.assert_awaited_once()


async def test_load_jwks_refetches_when_expired(
    mock_httpx: Any, mock_jwks_data: Any
) -> None:
    """An expired cache triggers a fresh HTTP fetch."""
    oidc._jwks_cache["keys"] = {"keys": [{"kid": "old"}]}
    oidc._jwks_cache["expires_at"] = time.time() - 1

    result = await _load_jwks()

    assert result == mock_jwks_data
    mock_httpx.get.assert_awaited_once()


async def test_load_jwks_returns_cached_when_valid(
    mock_httpx: Any, mock_jwks_data: Any
) -> None:
    """A valid (non-expired) cache is returned without an HTTP call."""
    oidc._jwks_cache["keys"] = mock_jwks_data
    oidc._jwks_cache["expires_at"] = time.time() + 9999

    result = await _load_jwks()

    assert result == mock_jwks_data
    mock_httpx.get.assert_not_awaited()


# ---------------------------------------------------------------------------
# validate_oidc_token — branch coverage (mocked crypto)
# ---------------------------------------------------------------------------


async def test_validate_force_refresh_on_kid_miss(payload: dict[str, Any]) -> None:
    """Kid absent from the initial JWKS: force refresh and find it on retry."""
    jwks_no_match = {"keys": [{"kid": "other"}]}
    jwks_match = {"keys": [{"kid": "kid-1", "kty": "RSA"}]}
    rsa_key = MagicMock(spec=RSAPublicKey)
    load_mock = AsyncMock(side_effect=[jwks_no_match, jwks_match])

    with (
        patch.object(oidc, "_load_jwks", load_mock),
        patch.object(jwt, "get_unverified_header", return_value={"kid": "kid-1"}),
        patch.object(RSAAlgorithm, "from_jwk", return_value=rsa_key),
        patch.object(jwt, "decode", return_value=payload),
    ):
        result = await validate_oidc_token("tok")

    assert result == payload
    assert load_mock.await_count == 2
    load_mock.assert_any_await(force=True)


async def test_validate_raises_on_non_rsa_key() -> None:
    """A non-RSA key from JWKS raises `InvalidTokenError`."""
    jwks = {"keys": [{"kid": "kid-1", "kty": "RSA"}]}
    not_rsa = MagicMock()

    with (
        patch.object(oidc, "_load_jwks", new_callable=AsyncMock, return_value=jwks),
        patch.object(jwt, "get_unverified_header", return_value={"kid": "kid-1"}),
        patch.object(RSAAlgorithm, "from_jwk", return_value=not_rsa),
        pytest.raises(InvalidTokenError, match="Expected RSA public key"),
    ):
        await validate_oidc_token("tok")


async def test_validate_raises_when_kid_not_found() -> None:
    """Kid missing even after a forced refresh raises `InvalidTokenError`."""
    jwks = {"keys": [{"kid": "other"}]}
    load_mock = AsyncMock(return_value=jwks)

    with (
        patch.object(oidc, "_load_jwks", load_mock),
        patch.object(jwt, "get_unverified_header", return_value={"kid": "kid-1"}),
        pytest.raises(InvalidTokenError, match="Matching key not found"),
    ):
        await validate_oidc_token("tok")


# ---------------------------------------------------------------------------
# validate_oidc_token — end-to-end with a real RSA keypair
# ---------------------------------------------------------------------------


async def test_validate_real_token_success(
    rsa_keypair: tuple[RSAPrivateKey, dict[str, Any]],
) -> None:
    """A correctly-signed, unexpired token is decoded to its claims."""
    private_key, jwk = rsa_keypair
    token = _sign(private_key, jwk["kid"], oid="o-1", tid="t-1")

    with patch.object(
        oidc, "_load_jwks", new_callable=AsyncMock, return_value={"keys": [jwk]}
    ):
        claims = await validate_oidc_token(token)

    assert claims["sub"] == "subject-1"
    assert claims["oid"] == "o-1"
    assert claims["tid"] == "t-1"


async def test_validate_rejects_expired_token(
    rsa_keypair: tuple[RSAPrivateKey, dict[str, Any]],
) -> None:
    """Expiry is now enforced: a token past its `exp` is rejected."""
    private_key, jwk = rsa_keypair
    token = _sign(private_key, jwk["kid"], exp=int(time.time()) - 10)

    with (
        patch.object(
            oidc, "_load_jwks", new_callable=AsyncMock, return_value={"keys": [jwk]}
        ),
        pytest.raises(ExpiredSignatureError),
    ):
        await validate_oidc_token(token)
