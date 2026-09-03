"""Auth internal token utilities.

This module implements the core logic for creating and verifying the app's own
short-lived access tokens and longer-lived refresh tokens. Tokens are signed
symmetrically (HS256) with `JWT_SECRET`, with the algorithm hard-pinned to
prevent algorithm-confusion / `none` attacks. Each token carries the identity
claims (`uid`, `oid`, `tid`, ...) needed to rebuild a stable `User`, plus a
`type` claim distinguishing access from refresh tokens.
"""

import time
from typing import Any
from uuid import uuid4

import jwt
from jwt.exceptions import InvalidTokenError

from app.auth.config import auth_settings
from app.auth.schemas import TokenClaims

ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"  # nosec B105
REFRESH_TOKEN_TYPE = "refresh"  # nosec B105


def _create_token(claims: TokenClaims, *, token_type: str, duration: int) -> str:
    """Sign an app token of the given type with the configured secret.

    Args:
        claims (TokenClaims): Identity claims to embed.
        token_type (str): Either `ACCESS_TOKEN_TYPE` or `REFRESH_TOKEN_TYPE`.
        duration (int): Token lifetime in seconds.

    Returns:
        str: The encoded JWT.
    """
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": auth_settings.JWT_ISSUER,
        "aud": auth_settings.JWT_AUDIENCE,
        "sub": claims.sub,
        "iat": now,
        "nbf": now,
        "exp": now + duration,
        "jti": uuid4().hex,
        "type": token_type,
        "uid": claims.uid,
        "name": claims.name,
        "preferred_username": claims.preferred_username,
        "oid": claims.oid,
        "tid": claims.tid,
    }
    return jwt.encode(
        payload, auth_settings.JWT_SECRET.get_secret_value(), algorithm=ALGORITHM
    )


def create_access_token(claims: TokenClaims) -> str:
    """Create a short-lived access token.

    Args:
        claims (TokenClaims): Identity claims to embed.

    Returns:
        str: The encoded access JWT.
    """
    return _create_token(
        claims,
        token_type=ACCESS_TOKEN_TYPE,
        duration=auth_settings.JWT_ACCESS_TOKEN_DURATION_S,
    )


def create_refresh_token(claims: TokenClaims) -> str:
    """Create a longer-lived refresh token.

    Args:
        claims (TokenClaims): Identity claims to embed.

    Returns:
        str: The encoded refresh JWT.
    """
    return _create_token(
        claims,
        token_type=REFRESH_TOKEN_TYPE,
        duration=auth_settings.JWT_REFRESH_TOKEN_DURATION_S,
    )


def decode_internal_token(token: str, *, expected_type: str) -> dict[str, Any]:
    """Verify an app-issued token and return its claims.

    Validates the signature, issuer, audience and expiry, then asserts the
    token is of the expected type.

    Args:
        token (str): The encoded JWT.
        expected_type (str): The required `type` claim.

    Returns:
        dict[str, Any]: The verified token claims.

    Raises:
        InvalidTokenError: If verification fails or the type does not match.
    """
    payload: dict[str, Any] = jwt.decode(
        token,
        auth_settings.JWT_SECRET.get_secret_value(),
        algorithms=[ALGORITHM],
        audience=auth_settings.JWT_AUDIENCE,
        issuer=auth_settings.JWT_ISSUER,
    )
    if payload.get("type") != expected_type:
        raise InvalidTokenError(f"Expected token of type '{expected_type}'")
    return payload
