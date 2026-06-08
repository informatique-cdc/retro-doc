"""Unit test configuration for repos.

This module provides mock-based fixtures for isolated tests and
database-backed fixtures (via mongomock) for tests that verify
persistence behaviour.
"""

from collections.abc import Generator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from app.auth.dependencies import get_current_user
from app.auth.schemas import User
from app.core.language_enum import Language
from app.repos.dependencies import get_verified_file, get_verified_repo
from app.repos.models import FileDocument, RepoDocument
from app.users.dependencies import get_user_repo
from app.users.models import UserRepoDocument


@pytest.fixture
def _override_deps(
    mock_client: Any,
    user: User,
    user_repo_doc: UserRepoDocument,
    repo_doc: RepoDocument,
    file_doc: FileDocument,
) -> Generator[None, None, None]:
    """Override FastAPI dependencies for repos HTTP tests."""
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_user_repo] = lambda: user_repo_doc
    app.dependency_overrides[get_verified_repo] = lambda: repo_doc
    app.dependency_overrides[get_verified_file] = lambda: file_doc
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def mock_blob_container() -> Any:
    """Patch `get_container_client` to return an AsyncMock container."""
    mock_container = AsyncMock()
    with patch("app.repos.service.get_container_client", return_value=mock_container):
        yield mock_container


@pytest.fixture
def mock_file_doc(file_id: PydanticObjectId, repo_id: PydanticObjectId) -> MagicMock:
    """A mocked `FileDocument` for unit tests."""
    file_doc = MagicMock(spec=FileDocument)
    file_doc.id = file_id
    file_doc.repo_id = repo_id
    file_doc.path = "src/Main.java"
    file_doc.file_hash = "abc123"
    return file_doc


@pytest.fixture
def mock_repo_doc(repo_id: PydanticObjectId, blob_path: str) -> MagicMock:
    """A mocked `RepoDocument` for unit tests."""
    repo = MagicMock(spec=RepoDocument)
    repo.id = repo_id
    repo.repo_url = "code.zip"
    repo.repo_branch = None
    repo.repo_hash = None
    repo.blob_path = blob_path
    repo.language = Language.JAVA
    repo.user_count = 1
    repo.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    repo.updated_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    repo.update = AsyncMock()
    return repo


@pytest.fixture
def mock_start_orchestration() -> Any:
    """Patch `start_orchestration` to a no-op AsyncMock."""
    with patch(
        "app.repos.service.start_orchestration", new_callable=AsyncMock
    ) as mock_orch:
        yield mock_orch


@pytest.fixture
async def persisted_file_doc(
    persisted_repo_doc: RepoDocument, file_doc: FileDocument
) -> FileDocument:
    """A `FileDocument` persisted in mongomock."""
    await file_doc.insert()
    return file_doc


@pytest.fixture
async def persisted_repo_doc(repo_doc: RepoDocument) -> RepoDocument:
    """A `RepoDocument` persisted in mongomock."""
    await repo_doc.insert()
    return repo_doc


@pytest.fixture
async def persisted_user_repo_doc(
    user_repo_doc: UserRepoDocument,
) -> UserRepoDocument:
    """A `UserRepoDocument` persisted in mongomock."""
    await user_repo_doc.insert()
    return user_repo_doc


@pytest.fixture
async def two_persisted_user_repo_docs(
    user: User,
) -> tuple[UserRepoDocument, UserRepoDocument]:
    """Two `RepoDocument`/`UserRepoDocument` pairs persisted in mongomock."""
    repo_a = RepoDocument(blob_path="a", language="java")
    repo_b = RepoDocument(blob_path="b", language="java")
    await repo_a.insert()
    await repo_b.insert()

    ur_a = UserRepoDocument(user_id=user.uid, repo_id=repo_a.id, name="repo-a")
    ur_b = UserRepoDocument(user_id=user.uid, repo_id=repo_b.id, name="repo-b")
    await ur_a.insert()
    await ur_b.insert()

    return ur_a, ur_b
