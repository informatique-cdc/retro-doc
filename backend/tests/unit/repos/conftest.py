"""Unit test configuration for repos.

This module provides database-backed fixtures (via mongomock)
shared by several repos test modules.
"""

import pytest

from app.repos.models import FileDocument, RepoDocument


@pytest.fixture
async def persisted_file_doc(
    persisted_repo_doc: RepoDocument, file_doc: FileDocument
) -> FileDocument:
    """A persisted `FileDocument`."""
    await file_doc.insert()
    return file_doc


@pytest.fixture
async def persisted_repo_doc(repo_doc: RepoDocument) -> RepoDocument:
    """A persisted `RepoDocument`."""
    await repo_doc.insert()
    return repo_doc
