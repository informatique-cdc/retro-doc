"""Auth identity derivation.

This module owns the derivation of the stable unique user id from identity
claims, serving as the single source of truth shared by login and the `User`
model.
"""

import hashlib


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
