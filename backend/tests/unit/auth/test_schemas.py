"""Unit tests for auth schemas.

This module tests the auth schemas.
"""

import hashlib
from typing import Any

import pytest

from app.auth.schemas import User


class TestUserUid:
    """Stable per-user identifier derived from a `User`'s own claims."""

    def test_uid_differs_for_different_users(self, payload: dict[str, Any]) -> None:
        """Users with different oid values produce different uids."""
        a = User(**payload)
        b = User(**{**payload, "oid": "different-oid"})

        assert a.uid != b.uid

    @pytest.mark.parametrize(
        "overrides",
        [
            pytest.param({"oid": None}, id="oid-missing"),
            pytest.param({"tid": None}, id="tid-missing"),
            pytest.param({"oid": None, "tid": None}, id="both-missing"),
        ],
    )
    def test_uid_fallback(
        self, payload: dict[str, Any], overrides: dict[str, None]
    ) -> None:
        """UID falls back to oidc:{iss}:{sub} when Azure fields are absent."""
        user = User(**{**payload, **overrides})

        raw = f"oidc:{payload['iss']}:{payload['sub']}"
        assert user.uid == hashlib.sha256(raw.encode()).hexdigest()

    def test_uid_from_azure_oid_tid(self, payload: dict[str, Any]) -> None:
        """UID is derived from azure:{tid}:{oid} when both are present."""
        user = User(**payload)

        expected = hashlib.sha256(
            f"azure:{payload['tid']}:{payload['oid']}".encode()
        ).hexdigest()
        assert user.uid == expected

    def test_uid_is_deterministic(self, payload: dict[str, Any]) -> None:
        """Two identical Users produce the same uid."""
        a = User(**payload)
        b = User(**payload)

        assert a.uid == b.uid

    def test_provided_uid_is_trusted(self, payload: dict[str, Any]) -> None:
        """An explicitly provided uid is preserved, not recomputed.

        This is what lets an app-issued token carry the original uid forward.
        """
        user = User(**{**payload, "uid": "explicit-uid"})

        assert user.uid == "explicit-uid"
