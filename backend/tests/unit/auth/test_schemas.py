"""Unit tests for auth schemas.

This module tests the auth schemas.
"""

import hashlib
from typing import Any

import pytest

from app.auth.schemas import User


def test_uid_differs_for_different_users(payload: dict[str, Any]) -> None:
    """Users with different oid values produce different uids."""
    a = User(**payload)
    b = User(**{**payload, "oid": "different-oid"})

    assert a.uid != b.uid


@pytest.mark.parametrize(
    "overrides",
    [
        {"oid": None},
        {"tid": None},
        {"oid": None, "tid": None},
    ],
    ids=["oid_missing", "tid_missing", "both_missing"],
)
def test_uid_fallback(payload: dict[str, Any], overrides: dict[str, None]) -> None:
    """UID falls back to oidc:{iss}:{sub} when Azure fields are absent."""
    user = User(**{**payload, **overrides})

    raw = f"oidc:{payload['iss']}:{payload['sub']}"
    assert user.uid == hashlib.sha256(raw.encode()).hexdigest()


def test_uid_from_azure_oid_tid(payload: dict[str, Any]) -> None:
    """UID is derived from azure:{tid}:{oid} when both are present."""
    user = User(**payload)

    expected = hashlib.sha256(
        f"azure:{payload['tid']}:{payload['oid']}".encode()
    ).hexdigest()
    assert user.uid == expected


def test_uid_is_deterministic(payload: dict[str, Any]) -> None:
    """Two identical Users produce the same uid."""
    a = User(**payload)
    b = User(**payload)

    assert a.uid == b.uid
