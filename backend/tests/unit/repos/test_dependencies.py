"""Unit tests for repos dependencies.

This module tests the repos dependencies against a mongomock database.
"""

from unittest.mock import MagicMock

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from app.repos.dependencies import get_verified_file, get_verified_repo
from app.repos.models import FileDocument, RepoDocument

# ---------------------------------------------------------------------------
# get_verified_repo
# ---------------------------------------------------------------------------


async def test_get_verified_repo_raises_404_when_not_found(
    repo_id: PydanticObjectId,
) -> None:
    """Raises HTTP 404 when the repository is not in the database."""
    with pytest.raises(HTTPException) as exc_info:
        await get_verified_repo(repo_id=repo_id, _user_repo=MagicMock())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Repository not found."


async def test_get_verified_repo_returns_when_exists(
    persisted_repo_doc: RepoDocument,
    repo_id: PydanticObjectId,
) -> None:
    """Returns the RepoDocument from the database."""
    result = await get_verified_repo(repo_id=repo_id, _user_repo=MagicMock())

    assert result.id == repo_id
    assert result.blob_path == persisted_repo_doc.blob_path


# ---------------------------------------------------------------------------
# get_verified_file
# ---------------------------------------------------------------------------


async def test_get_verified_file_raises_404_when_not_found(
    file_id: PydanticObjectId,
    repo_id: PydanticObjectId,
) -> None:
    """Raises HTTP 404 when the file is not in the database."""
    with pytest.raises(HTTPException) as exc_info:
        await get_verified_file(
            file_id=file_id, repo_id=repo_id, _user_repo=MagicMock()
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "File not found in this repository."


async def test_get_verified_file_returns_when_exists(
    persisted_file_doc: FileDocument,
    file_id: PydanticObjectId,
    repo_id: PydanticObjectId,
) -> None:
    """Returns the FileDocument from the database."""
    result = await get_verified_file(
        file_id=file_id, repo_id=repo_id, _user_repo=MagicMock()
    )

    assert result.id == file_id
    assert result.repo_id == repo_id
