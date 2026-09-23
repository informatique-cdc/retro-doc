"""Unit test configuration.

This module exposes shared fixtures for all tests.
It is automatically imported by pytest and applies to all tests in the suite.
Fake environment variables are set in pyproject.toml via pytest-env.
"""

from collections.abc import AsyncGenerator, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import mongomock.collection
import mongomock.database
import pytest
from beanie import PydanticObjectId, init_beanie
from httpx import ASGITransport
from loguru import logger
from mongomock_motor import AsyncMongoMockClient
from pymongo import IndexModel

from app import main
from app.auth.schemas import User
from app.chat.models import ChatMessageDocument, ChatThreadDocument
from app.deep_analysis.models import DeepAnalysisDocument
from app.docs.models import FileDocumentationDocument, RepoMetaDocument
from app.graphs.models import ASTDocument, CFGDocument, DFGDocument
from app.pipeline.models import PipelineRunDocument
from app.repos.models import FileDocument, RepoDocument
from app.users.models import UserRepoDocument


@pytest.fixture(autouse=True)
async def _init_beanie(_mongomock_beanie_compat: None) -> None:
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
            RepoMetaDocument,
            PipelineRunDocument,
            RepoDocument,
            UserRepoDocument,
        ],
    )


@pytest.fixture
def _mongomock_beanie_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Close two gaps between mongomock and the driver beanie 2.x expects.

    `list_collection_names`: `init_beanie` passes `authorizedCollections` and
    `nameOnly`, which mongomock 4.3.0 rejects with `TypeError`. Dropped them.

    `create_indexes`: mongomock drops `partialFilterExpression`, turning every
    partial unique index into a total one that rejects writes production
    accepts. Forwarded it.
    """
    original = mongomock.database.Database.list_collection_names

    def _list_collection_names(
        self: mongomock.database.Database,
        filter: dict[str, Any] | None = None,
        session: Any = None,
        **_kwargs: Any,
    ) -> list[str]:
        # mongomock is untyped, so the delegated call is seen as untyped/Any.
        return original(  # type: ignore[no-any-return, no-untyped-call]
            self, filter=filter, session=session
        )

    def _create_indexes(
        self: mongomock.collection.Collection,
        indexes: list[IndexModel],
        session: Any = None,
    ) -> list[str]:
        return [
            self.create_index(  # type: ignore[no-untyped-call]
                index.document["key"].items(),
                session=session,
                expireAfterSeconds=index.document.get("expireAfterSeconds"),
                unique=index.document.get("unique", False),
                sparse=index.document.get("sparse", False),
                name=index.document.get("name"),
                partialFilterExpression=index.document.get("partialFilterExpression"),
            )
            for index in indexes
        ]

    monkeypatch.setattr(
        mongomock.database.Database, "list_collection_names", _list_collection_names
    )
    monkeypatch.setattr(
        mongomock.collection.Collection, "create_indexes", _create_indexes
    )


@pytest.fixture(scope="session", autouse=True)
def _suppress_loguru() -> None:
    """Disable loguru output for the whole session to keep output clean."""
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
    )


@pytest.fixture
def file_id() -> PydanticObjectId:
    """A fixed file ID for tests."""
    return PydanticObjectId("000000000000000000000002")


@pytest.fixture
async def mock_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client wired to the FastAPI app, with lifespan mocked."""

    monkeypatch.setattr(main, "init_blob_storage", MagicMock())
    monkeypatch.setattr(main, "init_database", AsyncMock())
    monkeypatch.setattr(main, "close_database", AsyncMock())
    monkeypatch.setattr(main, "close_blob_storage", AsyncMock())

    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test/api/v0"
    ) as async_client:
        yield async_client


@pytest.fixture
def mock_httpx(monkeypatch: pytest.MonkeyPatch) -> Callable[[AsyncMock], AsyncMock]:
    """Install a built client as `httpx.AsyncClient` for the duration of a test.

    Takes a client from `tests.unit.mocks.httpx_client` and returns it, so a
    test both installs and keeps a handle on it in one expression.
    """

    def _install(client: AsyncMock) -> AsyncMock:
        monkeypatch.setattr(httpx, "AsyncClient", lambda *_args, **_kwargs: client)
        return client

    return _install


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
    """A `RepoDocument` with deterministic ID and fields.

    Stamped by the worker, the steady state of an analyzed repository: the
    unstamped window between dispatch and the worker's first activity is a
    transient a test opts into by clearing `ran_analyzer_version`.
    """
    return RepoDocument(
        id=repo_id,
        repo_url="code.zip",
        blob_path=blob_path,
        analyzer_version="v1",
        ran_analyzer_version="v1",
        languages=["java"],
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
