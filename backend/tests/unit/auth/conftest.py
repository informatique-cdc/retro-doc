"""Unit test configuration for auth.

This module provides fixtures shared by several auth test modules.
"""

import pytest

from app.auth.schemas import TokenClaims


@pytest.fixture
def token_claims() -> TokenClaims:
    """Identity claims for issuing app tokens, matching the `payload` fixture."""
    return TokenClaims(
        uid="stable-user-uid",
        sub="test-subject-id",
        name="Test User",
        preferred_username="testuser",
        oid="00000000-0000-0000-0000-000000000001",
        tid="00000000-0000-0000-0000-000000000002",
    )
