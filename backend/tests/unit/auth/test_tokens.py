"""Unit tests for internal (app-issued) JWT issuance and verification.

Covers the round-trip, the access/refresh `type` separation, expiry, and the
security guarantees: signature checks, algorithm pinning (no `alg=none`), and
the all-important stability of `User.uid` across a token round-trip.
"""

import base64
import json
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest
from jwt.exceptions import (
    ExpiredSignatureError,
    InvalidSignatureError,
    InvalidTokenError,
)
from pydantic import SecretStr

from app.auth.config import auth_settings
from app.auth.schemas import TokenClaims, User
from app.auth.tokens import (
    ACCESS_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_internal_token,
)
from app.auth.utils import compute_uid


def _unsigned_token(payload: dict[str, Any]) -> str:
    """Forge an `alg=none`, unsigned JWT for the algorithm-confusion test."""

    def _seg(data: dict[str, Any]) -> str:
        raw = json.dumps(data).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{_seg({'alg': 'none', 'typ': 'JWT'})}.{_seg(payload)}."


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_access_token_round_trip(token_claims: TokenClaims) -> None:
    """An access token decodes back to its claims with `type=access`."""
    token = create_access_token(token_claims)

    payload = decode_internal_token(token, expected_type=ACCESS_TOKEN_TYPE)

    assert payload["type"] == ACCESS_TOKEN_TYPE
    assert payload["uid"] == token_claims.uid
    assert payload["sub"] == token_claims.sub
    assert payload["oid"] == token_claims.oid
    assert payload["tid"] == token_claims.tid
    assert payload["iss"] == auth_settings.JWT_ISSUER
    assert payload["aud"] == auth_settings.JWT_AUDIENCE
    assert "jti" in payload


def test_refresh_token_round_trip(token_claims: TokenClaims) -> None:
    """A refresh token decodes back with `type=refresh`."""
    token = create_refresh_token(token_claims)

    payload = decode_internal_token(token, expected_type=REFRESH_TOKEN_TYPE)

    assert payload["type"] == REFRESH_TOKEN_TYPE
    assert payload["uid"] == token_claims.uid


# ---------------------------------------------------------------------------
# Type separation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("make_token", "expected_type", "match"),
    [
        (create_access_token, REFRESH_TOKEN_TYPE, "Expected token of type 'refresh'"),
        (create_refresh_token, ACCESS_TOKEN_TYPE, "Expected token of type 'access'"),
    ],
    ids=["access_as_refresh", "refresh_as_access"],
)
def test_token_type_mismatch_rejected(
    token_claims: TokenClaims,
    make_token: Callable[[TokenClaims], str],
    expected_type: str,
    match: str,
) -> None:
    """Decoding a token while expecting the other `type` fails."""
    token = make_token(token_claims)

    with pytest.raises(InvalidTokenError, match=match):
        decode_internal_token(token, expected_type=expected_type)


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_expired_token_rejected(token_claims: TokenClaims) -> None:
    """A token whose `exp` is in the past is rejected."""
    with patch.object(auth_settings, "JWT_ACCESS_TOKEN_DURATION_S", -10):
        token = create_access_token(token_claims)

    with pytest.raises(ExpiredSignatureError):
        decode_internal_token(token, expected_type=ACCESS_TOKEN_TYPE)


@pytest.mark.parametrize(
    ("setting", "value", "exception"),
    [
        (
            "JWT_SECRET",
            SecretStr("a-different-fake-secret-at-least-32-bytes"),
            InvalidSignatureError,
        ),
        ("JWT_ISSUER", "some-other-issuer", InvalidTokenError),
    ],
    ids=["bad_signature", "wrong_issuer"],
)
def test_decode_rejects_tampered_setting(
    token_claims: TokenClaims,
    setting: str,
    value: object,
    exception: type[Exception],
) -> None:
    """A token valid at issue time is rejected when a security-critical setting
    (`JWT_SECRET` / `JWT_ISSUER`) differs at decode time."""
    token = create_access_token(token_claims)

    with (
        patch.object(auth_settings, setting, value),
        pytest.raises(exception),
    ):
        decode_internal_token(token, expected_type=ACCESS_TOKEN_TYPE)


def test_alg_none_rejected(token_claims: TokenClaims) -> None:
    """An unsigned `alg=none` token is rejected (algorithm is pinned)."""
    forged = _unsigned_token(
        {
            "iss": auth_settings.JWT_ISSUER,
            "aud": auth_settings.JWT_AUDIENCE,
            "sub": token_claims.sub,
            "uid": token_claims.uid,
            "type": ACCESS_TOKEN_TYPE,
        }
    )

    with pytest.raises(InvalidTokenError):
        decode_internal_token(forged, expected_type=ACCESS_TOKEN_TYPE)


# ---------------------------------------------------------------------------
# uid stability — the critical correctness guarantee
# ---------------------------------------------------------------------------


def test_uid_is_stable_across_token_round_trip() -> None:
    """A `User` rebuilt from an access token keeps the original Azure `uid`.

    This protects every user's access to their existing data: the internal
    token carries the original `oid`/`tid` (and the precomputed `uid`), so the
    request-time `User.uid` matches the one issued at login even though the
    token's own issuer is now the app, not Microsoft.
    """
    oid = "11111111-1111-1111-1111-111111111111"
    tid = "22222222-2222-2222-2222-222222222222"
    sub = "subject-123"
    ms_issuer = f"https://login.microsoftonline.com/{tid}/v2.0"

    original_uid = compute_uid(oid=oid, tid=tid, iss=ms_issuer, sub=sub)

    token = create_access_token(
        TokenClaims(uid=original_uid, sub=sub, oid=oid, tid=tid)
    )
    payload = decode_internal_token(token, expected_type=ACCESS_TOKEN_TYPE)
    rebuilt = User(**payload)

    assert rebuilt.uid == original_uid
    # Equal to a User built directly from the Microsoft-style identity.
    direct = User(
        iss=ms_issuer,
        sub=sub,
        aud="aud",
        exp=9999999999,
        nbf=0,
        iat=0,
        oid=oid,
        tid=tid,
    )
    assert rebuilt.uid == direct.uid
