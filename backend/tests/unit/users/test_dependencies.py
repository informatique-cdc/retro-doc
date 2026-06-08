"""Unit tests for users dependencies.

This module tests the users related dependencies.
"""

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from app.auth.schemas import User
from app.users.dependencies import get_user_repo
from app.users.models import UserRepoDocument


async def test_returns_document_when_exists(
    user: User,
    repo_id: PydanticObjectId,
    user_repo_doc: UserRepoDocument,
) -> None:
    """Test that `get_user_repo` returns the document when it exists."""
    await user_repo_doc.insert()

    result = await get_user_repo(repo_id=repo_id, user=user)

    assert result.user_id == user.uid
    assert result.repo_id == repo_id
    assert result.name == "test-repo"


async def test_raises_404_when_no_link(user: User, repo_id: PydanticObjectId) -> None:
    """Test that `get_user_repo` raises 404 when no `UserRepoDocument` links
    the user to the repo.
    """
    with pytest.raises(HTTPException) as exc_info:
        await get_user_repo(repo_id=repo_id, user=user)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Repository not found."
