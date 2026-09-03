"""Unit tests for auth utilities."""

import hashlib

import pytest

from app.auth.utils import compute_uid


@pytest.mark.parametrize(
    ("oid", "tid", "expected_raw"),
    [
        ("o", "t", b"azure:t:o"),
        (None, None, b"oidc:issuer:sub"),
    ],
    ids=["azure_when_oid_and_tid", "oidc_fallback_without_oid_tid"],
)
def test_compute_uid(oid: str | None, tid: str | None, expected_raw: bytes) -> None:
    """`compute_uid` prefers `azure:{tid}:{oid}` and falls back to `oidc:{iss}:{sub}`."""
    uid = compute_uid(oid=oid, tid=tid, iss="issuer", sub="sub")

    assert uid == hashlib.sha256(expected_raw).hexdigest()
