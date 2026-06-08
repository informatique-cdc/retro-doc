"""Unit tests for auth dependencies.

This module test auth dependencies.
"""

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jwt.algorithms import RSAAlgorithm
from jwt.exceptions import InvalidTokenError

from app.auth import dependencies as deps
from app.auth.config import auth_settings
from app.auth.dependencies import (
    _decode_token,
    _load_jwks,
    get_current_user,
)
from app.core.config import settings


def _make_credentials(token: str = "fake-token") -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# ---------------------------------------------------------------------------
# _load_jwks
# ---------------------------------------------------------------------------


async def test_load_jwks_double_check_inside_lock(
    mock_httpx: Any, mock_jwks_data: Any
) -> None:
    """A concurrent caller that enters the lock after the cache was populated
    returns early without making an HTTP call (double-check pattern)."""
    # Hold the lock, then start _load_jwks which will block on it.
    async with deps._jwks_lock:
        task = asyncio.create_task(_load_jwks())
        await asyncio.sleep(0.05)
        # Populate cache while _load_jwks is waiting for the lock.
        deps._jwks_cache["keys"] = mock_jwks_data
        deps._jwks_cache["expires_at"] = time.time() + 9999

    # Lock released — _load_jwks enters and hits the inner check.
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
    assert deps._jwks_cache["keys"] == mock_jwks_data


async def test_load_jwks_force_bypasses_cache(
    mock_httpx: Any, mock_jwks_data: Any
) -> None:
    """`force=True` fetches even when the cache is still valid."""
    deps._jwks_cache["keys"] = {"keys": [{"kid": "old"}]}
    deps._jwks_cache["expires_at"] = time.time() + 9999

    result = await _load_jwks(force=True)

    assert result == mock_jwks_data
    mock_httpx.get.assert_awaited_once()


async def test_load_jwks_refetches_when_expired(
    mock_httpx: Any, mock_jwks_data: Any
) -> None:
    """An expired cache triggers a fresh HTTP fetch."""
    deps._jwks_cache["keys"] = {"keys": [{"kid": "old"}]}
    deps._jwks_cache["expires_at"] = time.time() - 1

    result = await _load_jwks()

    assert result == mock_jwks_data
    mock_httpx.get.assert_awaited_once()


async def test_load_jwks_returns_cached_when_valid(
    mock_httpx: Any, mock_jwks_data: Any
) -> None:
    """A valid (non-expired) cache is returned without an HTTP call."""
    deps._jwks_cache["keys"] = mock_jwks_data
    deps._jwks_cache["expires_at"] = time.time() + 9999

    result = await _load_jwks()

    assert result == mock_jwks_data
    mock_httpx.get.assert_not_awaited()


# ---------------------------------------------------------------------------
# _decode_token
# ---------------------------------------------------------------------------


async def test_decode_token_force_refresh_on_kid_miss(
    payload: dict[str, Any],
) -> None:
    """Kid not in initial JWKS, force refresh and found on retry."""
    jwks_no_match = {"keys": [{"kid": "other"}]}
    jwks_match = {"keys": [{"kid": "kid-1", "kty": "RSA"}]}
    rsa_key = MagicMock(spec=RSAPublicKey)

    load_mock = AsyncMock(side_effect=[jwks_no_match, jwks_match])

    with (
        patch.object(deps, "_load_jwks", load_mock),
        patch.object(jwt, "get_unverified_header", return_value={"kid": "kid-1"}),
        patch.object(RSAAlgorithm, "from_jwk", return_value=rsa_key),
        patch.object(jwt, "decode", return_value=payload),
    ):
        result = await _decode_token("tok")

    assert result == payload
    assert load_mock.await_count == 2
    load_mock.assert_any_await(force=True)


async def test_decode_token_raises_on_non_rsa_key() -> None:
    """A non-RSA key from JWKS raises `InvalidTokenError`."""
    jwks = {"keys": [{"kid": "kid-1", "kty": "RSA"}]}
    not_rsa = MagicMock()  # no RSAPublicKey spec

    with (
        patch.object(deps, "_load_jwks", new_callable=AsyncMock, return_value=jwks),
        patch.object(jwt, "get_unverified_header", return_value={"kid": "kid-1"}),
        patch.object(RSAAlgorithm, "from_jwk", return_value=not_rsa),
        pytest.raises(InvalidTokenError, match="Expected RSA public key"),
    ):
        await _decode_token("tok")


async def test_decode_token_raises_when_kid_not_found() -> None:
    """Kid not found even after forced refresh raises `InvalidTokenError`."""
    jwks = {"keys": [{"kid": "other"}]}
    load_mock = AsyncMock(return_value=jwks)

    with (
        patch.object(deps, "_load_jwks", load_mock),
        patch.object(jwt, "get_unverified_header", return_value={"kid": "kid-1"}),
        pytest.raises(InvalidTokenError, match="Matching key not found"),
    ):
        await _decode_token("tok")


async def test_decode_token_success(payload: dict[str, Any]) -> None:
    """Matching kid and RSA key returns the decoded payload."""
    jwks = {"keys": [{"kid": "kid-1", "kty": "RSA"}]}
    rsa_key = MagicMock(spec=RSAPublicKey)

    with (
        patch.object(deps, "_load_jwks", new_callable=AsyncMock, return_value=jwks),
        patch.object(jwt, "get_unverified_header", return_value={"kid": "kid-1"}),
        patch.object(RSAAlgorithm, "from_jwk", return_value=rsa_key),
        patch.object(jwt, "decode", return_value=payload),
    ):
        result = await _decode_token("tok")

    assert result == payload


# ---------------------------------------------------------------------------
# get_current_user
# ---------------------------------------------------------------------------


async def test_calls_decode_when_not_debug(payload: dict[str, Any]) -> None:
    """With `APP_DEBUG=False`, `_decode_token` is called and its payload used."""
    with (
        patch.object(settings, "APP_DEBUG", False),
        patch.object(
            deps, "_decode_token", new_callable=AsyncMock, return_value=payload
        ),
    ):
        user = await get_current_user(_make_credentials("my-token"))

    assert user.name == "Test User"


async def test_debug_mode_returns_debug_user() -> None:
    """Both debug flags `True` uses `gen_debug_payload` and skips `_decode_token`."""
    with (
        patch.object(settings, "APP_DEBUG", True),
        patch.object(auth_settings, "APP_AUTH_DEBUG", True),
        patch.object(deps, "_decode_token", new_callable=AsyncMock) as mock_decode,
    ):
        user = await get_current_user(_make_credentials())

    mock_decode.assert_not_awaited()
    assert user.name == "John Doe"
    assert user.uid is not None


async def test_debug_requires_both_flags(payload: dict[str, Any]) -> None:
    """`APP_DEBUG=True` and `APP_AUTH_DEBUG=False` make `_decode_token` called."""
    with (
        patch.object(settings, "APP_DEBUG", True),
        patch.object(auth_settings, "APP_AUTH_DEBUG", False),
        patch.object(
            deps, "_decode_token", new_callable=AsyncMock, return_value=payload
        ) as mock_decode,
    ):
        await get_current_user(_make_credentials())

    mock_decode.assert_awaited_once()


async def test_raises_401_on_decode_failure() -> None:
    """`InvalidTokenError` from `_decode_token` produces HTTP 401."""
    with (
        patch.object(settings, "APP_DEBUG", False),
        patch.object(
            deps,
            "_decode_token",
            new_callable=AsyncMock,
            side_effect=InvalidTokenError("bad"),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await get_current_user(_make_credentials())

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid authentication credentials"
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}
