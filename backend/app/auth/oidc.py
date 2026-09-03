"""Auth OpenID Connect (OIDC) token validation.

This module provides a utility function to validate externally-issued OIDC tokens
(e.g., Microsoft Entra ID) against a JWKS endpoint. It is used by identity
providers during the login flow to verify the credentials presented by clients
before issuing internal JWTs.
"""

import asyncio
import time
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.algorithms import RSAAlgorithm
from jwt.exceptions import InvalidTokenError

from app.auth.config import auth_settings

# Simple in-memory cache for JWKS with expiration
_jwks_cache: dict[str, Any] = {"keys": None, "expires_at": 0}
_jwks_lock = asyncio.Lock()


async def _load_jwks(force: bool = False) -> Any:
    """Load JWKS (JSON Web Key Set) from the configured URL.

    Args:
        force (bool): If `True`, forces a refresh of the JWKS cache even if it's still valid.

    Returns:
        Any: The JWKS data containing the keys used for token verification.
    """
    now = time.time()

    if not force and _jwks_cache["keys"] and now < _jwks_cache["expires_at"]:
        return _jwks_cache["keys"]

    async with _jwks_lock:
        # Double-check after acquiring the lock
        if not force and _jwks_cache["keys"] and now < _jwks_cache["expires_at"]:
            return _jwks_cache["keys"]

        async with httpx.AsyncClient() as client:
            resp = await client.get(auth_settings.OIDC_JWKS_URL, timeout=5)
        resp.raise_for_status()
        jwks_keys = resp.json()
        _jwks_cache["keys"] = jwks_keys
        _jwks_cache["expires_at"] = now + auth_settings.OIDC_JWKS_CACHE_TTL_S

        return jwks_keys


async def validate_oidc_token(token: str) -> dict[str, Any]:
    """Decode and verify an OIDC token using the JWKS keys.

    Unlike the previous implementation, token expiry IS enforced: a login may
    only be performed with a fresh, unexpired provider token.

    Args:
        token (str): The JWT token to decode and verify.

    Returns:
        dict[str, Any]: The decoded token payload if verification is successful.

    Raises:
        InvalidTokenError: If the token is invalid or verification fails.
    """
    jwks_keys = await _load_jwks()

    # Extract unverified header to get the signing key ID (kid)
    unverified_header = jwt.get_unverified_header(token)
    kid = unverified_header["kid"]

    # Find the signing key in JWKS that matches the token's kid
    key = next((k for k in jwks_keys["keys"] if k["kid"] == kid), None)
    if not key:
        # Retry with a fresh JWKS in case of key rotation
        jwks_keys = await _load_jwks(force=True)
        key = next((k for k in jwks_keys["keys"] if k["kid"] == kid), None)
    if not key:
        raise InvalidTokenError("Matching key not found in JWKS")

    public_key = RSAAlgorithm.from_jwk(key)
    if not isinstance(public_key, RSAPublicKey):
        raise InvalidTokenError("Expected RSA public key from JWKS")

    # Decode and verify the token (signature, audience, issuer and expiry)
    payload: dict[str, Any] = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        audience=auth_settings.OIDC_AUDIENCE,
        issuer=auth_settings.OIDC_ALLOWED_ISSUERS,
    )

    return payload
