"""Unit test configuration for languages.

This module overrides authentication so the protected languages router can be
exercised over HTTP without a real token.
"""

from collections.abc import Generator

import pytest

from app.auth.dependencies import get_current_user
from app.auth.schemas import User


@pytest.fixture
def _override_deps(user: User) -> Generator[None, None, None]:
    """Override FastAPI auth dependency for languages HTTP tests."""
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: user
    yield
    app.dependency_overrides.clear()
