"""Unit test configuration.

This module exposes shared fixtures for all tests.
It is automatically imported by pytest and applies to all tests in the suite.
Fake environment variables are set in pyproject.toml via pytest-env.
"""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from beanie import PydanticObjectId, init_beanie
from httpx import ASGITransport
from loguru import logger
from mongomock_motor import AsyncMongoMockClient

from app.auth.schemas import User
from app.chat.models import ChatMessageDocument, ChatThreadDocument
from app.core.language_enum import Language
from app.deep_analysis.models import DeepAnalysisDocument
from app.docs.models import FileDocumentationDocument, MetaRepoDocument
from app.graphs.models import ASTDocument, CFGDocument, DFGDocument
from app.pipeline.models import PipelineRunDocument
from app.repos.models import FileDocument, RepoDocument
from app.users.models import UserRepoDocument


@pytest.fixture(autouse=True)
async def _init_beanie() -> None:
    """Initialize Beanie with an in-memory mongomock MongoDB."""
    mongo_client: AsyncMongoMockClient = AsyncMongoMockClient()  # type: ignore[type-arg]
    await init_beanie(
        database=mongo_client["test_db"],  # type: ignore[arg-type]
        document_models=[
            ASTDocument,
            CFGDocument,
            ChatMessageDocument,
            ChatThreadDocument,
            DeepAnalysisDocument,
            DFGDocument,
            FileDocument,
            FileDocumentationDocument,
            MetaRepoDocument,
            PipelineRunDocument,
            RepoDocument,
            UserRepoDocument,
        ],
    )


@pytest.fixture(autouse=True)
def _suppress_loguru() -> None:
    """Disable loguru output during tests to keep output clean."""
    logger.remove()


@pytest.fixture
def blob_path() -> str:
    """A fixed blob storage path for tests."""
    return "repos/abc123/code.zip"


@pytest.fixture
def file_doc(file_id: PydanticObjectId, repo_id: PydanticObjectId) -> FileDocument:
    """A `FileDocument` with deterministic IDs."""
    return FileDocument(
        id=file_id,
        repo_id=repo_id,
        path="src/Main.java",
        file_hash="abc123",
    )


@pytest.fixture
def file_id() -> PydanticObjectId:
    """A fixed file ID for tests."""
    return PydanticObjectId("000000000000000000000002")


@pytest.fixture
async def mock_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client wired to the FastAPI app, with lifespan mocked."""
    with (
        patch("app.main.init_blob_storage"),
        patch("app.main.init_database", new_callable=AsyncMock),
        patch("app.main.close_database", new_callable=AsyncMock),
        patch("app.main.close_blob_storage", new_callable=AsyncMock),
    ):
        from app.main import app

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test/api/v0"
        ) as async_client:
            yield async_client


@pytest.fixture
def payload() -> dict[str, Any]:
    """A valid JWT payload matching the test OIDC environment."""
    return dict(
        iss="https://fake-issuer.example.com",
        sub="test-subject-id",
        aud="test-audience",
        exp=9999999999,
        nbf=0,
        iat=0,
        name="Test User",
        preferred_username="testuser",
        oid="00000000-0000-0000-0000-000000000001",
        tid="00000000-0000-0000-0000-000000000002",
    )


@pytest.fixture
def repo_doc(repo_id: PydanticObjectId, blob_path: str) -> RepoDocument:
    """A `RepoDocument` with deterministic ID and fields."""
    return RepoDocument(
        id=repo_id,
        blob_path=blob_path,
        language=Language.JAVA,
    )


@pytest.fixture
def repo_id() -> PydanticObjectId:
    """A fixed repository ID for tests."""
    return PydanticObjectId("000000000000000000000001")


@pytest.fixture
def user(payload: dict[str, Any]) -> User:
    """A deterministic `User` instance for tests that need authentication."""
    return User(**payload)


@pytest.fixture
def user_alt() -> User:
    """A second `User` distinct from the default `user` fixture."""
    return User(
        iss="https://fake-issuer.example.com",
        sub="other-subject",
        aud="test-audience",
        exp=9999999999,
        nbf=0,
        iat=0,
    )


@pytest.fixture
def user_repo_doc(user: User, repo_id: PydanticObjectId) -> UserRepoDocument:
    """A `UserRepoDocument` linking `user` to `repo_id`."""
    return UserRepoDocument(
        name="test-repo",
        user_id=user.uid,
        repo_id=repo_id,
    )
