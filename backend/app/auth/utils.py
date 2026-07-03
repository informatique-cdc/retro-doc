"""Auth utilities.

This module provides utility functions for authentication.
"""

import hashlib

from fastapi import HTTPException, status


def compute_uid(*, oid: str | None, tid: str | None, iss: str, sub: str) -> str:
    """Derive the stable unique user id from identity claims.

    Single source of truth shared by `User` and the login flow so an app-issued
    token reproduces exactly the `uid` a user already owns data under.

    Args:
        oid (str | None): Azure object id, if present.
        tid (str | None): Azure tenant id, if present.
        iss (str): Token issuer (used only in the non-Azure fallback).
        sub (str): Token subject (used only in the non-Azure fallback).

    Returns:
        str: The hex SHA-256 user id.
    """
    if oid and tid:
        raw = f"azure:{tid}:{oid}"
    else:
        raw = f"oidc:{iss}:{sub}"
    return hashlib.sha256(raw.encode()).hexdigest()


def create_debug_payload() -> dict[str, str | int]:
    """Create a fixed JWT payload for debugging purposes when authentication is disabled.

    Returns:
        dict[str, str | int]: A dictionary representing the JWT payload with fixed values.
    """
    return dict(
        iss="https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/v2.0",
        sub="12345678-1234-1234-1234-123456789012",
        aud="debug-client-id",
        exp=9999999999,
        nbf=0,
        iat=0,
        name="John Doe",
        preferred_username="johndoe",
        oid="12345678-1234-1234-1234-123456789012",
        tid="00000000-0000-0000-0000-00000",
    )


def invalid_credentials_exception() -> HTTPException:
    """Build the uniform 401 used for any authentication failure.

    Returns:
        HTTPException: A 401 Unauthorized exception with a standard message.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
