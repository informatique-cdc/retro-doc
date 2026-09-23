"""Unit tests for auth identity.

This module tests the auth identity derivation.
"""

import hashlib

import pytest

from app.auth.identity import compute_uid


class TestComputeUid:
    """Stable identity hash derived from the provider's claims."""

    @pytest.mark.parametrize(
        ("oid", "tid", "expected_raw"),
        [
            pytest.param("o", "t", b"azure:t:o", id="azure-when-oid-and-tid"),
            pytest.param(
                None, None, b"oidc:issuer:sub", id="oidc-fallback-without-oid-tid"
            ),
        ],
    )
    def test_compute_uid(
        self, oid: str | None, tid: str | None, expected_raw: bytes
    ) -> None:
        """`compute_uid` prefers `azure:{tid}:{oid}` and falls back to `oidc:{iss}:{sub}`."""
        uid = compute_uid(oid=oid, tid=tid, iss="issuer", sub="sub")

        assert uid == hashlib.sha256(expected_raw).hexdigest()
