"""Auth FastAPI dependencies.

This module implements the core security logic for authentication
and defines reusable FastAPI dependencies for user resolution.
"""

import asyncio
import time
from typing import Annotated, Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.algorithms import RSAAlgorithm
from jwt.exceptions import InvalidTokenError

from app.auth.config import auth_settings
from app.auth.schemas import User
from app.auth.utils import gen_debug_payload
from app.core.config import settings

# Define the HTTP Bearer scheme for FastAPI
http_bearer = HTTPBearer()

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
        _jwks_cache["expires_at"] = now + auth_settings.OIDC_JWKS_CACHE_TTL

        return jwks_keys


async def _decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT token using the JWKS keys.

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

    # Decode and verify the token
    payload = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        audience=auth_settings.OIDC_AUDIENCE,
        issuer=auth_settings.OIDC_ALLOWED_ISSUERS,
        options={"verify_exp": False},  # TODO: Managing JWT within the app?
    )

    return payload


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(http_bearer)],
) -> User:
    """Get the current authenticated user based on the provided JWT token.

    Args:
        credentials(HTTPAuthorizationCredentials): The HTTP Bearer credentials
            extracted from the Authorization header.

    Returns:
        User: The authenticated user represented as a User model instance.

    Raises:
        HTTPException: If the token is invalid or authentication fails,
            an HTTP 401 Unauthorized error is raised.
    """
    try:
        payload = (
            await _decode_token(credentials.credentials)
            if not (settings.APP_DEBUG and auth_settings.APP_AUTH_DEBUG)
            else gen_debug_payload()
        )
        return User(**payload)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


CurrentUser = Annotated[User, Depends(get_current_user)]
