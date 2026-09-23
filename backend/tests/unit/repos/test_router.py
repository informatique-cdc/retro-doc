"""Unit tests for repos router.

This module tests the repos router using `httpx.AsyncClient`
with mocked business logic and dependency overrides, plus direct
endpoint-function tests that don't need HTTP transport.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from beanie import PydanticObjectId

from app.auth.dependencies import get_current_user
from app.auth.schemas import User
from app.docs.models import AnalysisStats, RepoMetaDocument
from app.languages import service as languages_service
from app.pipeline.models import PipelineMeta, PipelineRunDocument, PipelineStatus
from app.repos import router
from app.repos.dependencies import get_verified_file, get_verified_repo
from app.repos.models import FileDocument, RepoDocument
from app.users.dependencies import get_verified_user_repo
from app.users.models import UserRepoDocument

pytestmark = pytest.mark.usefixtures("_override_deps", "mock_current_version")

_GIT_URL = "https://github.com/octo/repo"
_ZIP_FILE = {"file": ("code.zip", b"fake-zip-content", "application/zip")}


def _run(
    repo_id: PydanticObjectId,
    status: PipelineStatus,
    *,
    retried: bool = False,
    message: str | None = None,
) -> PipelineRunDocument:
    """A pipeline run shaped as the history query returns it."""
    return PipelineRunDocument(
        repo_id=repo_id,
        status=status,
        retried_at=datetime(2025, 1, 2, tzinfo=UTC) if retried else None,
        finished_at=datetime(2025, 1, 1, tzinfo=UTC) if message else None,
        meta=PipelineMeta(message=message, step="launch") if message else None,
    )


@pytest.fixture
def _override_deps(
    monkeypatch: pytest.MonkeyPatch,
    mock_client: httpx.AsyncClient,
    user: User,
    user_repo_doc: UserRepoDocument,
    repo_doc: RepoDocument,
    file_doc: FileDocument,
) -> None:
    """Override FastAPI dependencies for repos HTTP tests."""
    from app.main import app

    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: user)
    monkeypatch.setitem(
        app.dependency_overrides, get_verified_user_repo, lambda: user_repo_doc
    )
    monkeypatch.setitem(app.dependency_overrides, get_verified_repo, lambda: repo_doc)
    monkeypatch.setitem(app.dependency_overrides, get_verified_file, lambda: file_doc)


@pytest.fixture
def mock_current_version(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Pin the router's view of the worker version to the fixture repo's own."""
    mock_version = AsyncMock(return_value="v1")
    monkeypatch.setattr(router, "get_cached_analyzer_version", mock_version)
    return mock_version


class TestCreateRepoEndpoint:
    """`POST /repos` — start an analysis from a zip upload or a git remote."""

    @pytest.fixture
    def mock_create_repo(
        self, monkeypatch: pytest.MonkeyPatch, repo_id: PydanticObjectId
    ) -> AsyncMock:
        """Patch the router's `create_repo` to a pending, unjoined new analysis."""
        mock_create = AsyncMock(return_value=(repo_id, PipelineStatus.PENDING, False))
        monkeypatch.setattr(router, "create_repo", mock_create)
        return mock_create

    @pytest.mark.parametrize(
        "form_extra",
        [
            pytest.param({"languages": ["java"]}, id="with-language-filter"),
            pytest.param({}, id="all-languages"),
        ],
    )
    async def test_analyze_file_success(
        self,
        mock_client: httpx.AsyncClient,
        repo_id: PydanticObjectId,
        mock_create_repo: AsyncMock,
        form_extra: dict[str, Any],
    ) -> None:
        """A zip upload returns 202, with or without a language filter."""
        resp = await mock_client.post(
            "/repos",
            files=_ZIP_FILE,
            data={"name": "my-repo", **form_extra},
        )

        assert resp.status_code == 202
        data = resp.json()
        assert data["repo_id"] == str(repo_id)
        assert data["status"] == "pending"

    @pytest.mark.parametrize(
        ("form_extra", "expected_token"),
        [
            pytest.param({"token": "glpat-secret"}, "glpat-secret", id="private-repo"),
            pytest.param({}, None, id="public-repo"),
        ],
    )
    async def test_analyze_git_forwards_token(
        self,
        mock_client: httpx.AsyncClient,
        mock_create_repo: AsyncMock,
        form_extra: dict[str, str],
        expected_token: str | None,
    ) -> None:
        """A PAT on the create form reaches the service; omitting it forwards `None`."""
        resp = await mock_client.post(
            "/repos",
            data={"repo_url": _GIT_URL, "name": "my-repo", **form_extra},
        )

        assert resp.status_code == 202
        mock_create_repo.assert_awaited_once()
        assert mock_create_repo.await_args_list[0].kwargs["token"] == expected_token

    async def test_analyze_file_rejects_unsupported_language(
        self, monkeypatch: pytest.MonkeyPatch, mock_client: httpx.AsyncClient
    ) -> None:
        """An unsupported language is rejected with 422 (before any upload)."""
        monkeypatch.setattr(
            languages_service,
            "get_supported_languages",
            AsyncMock(return_value=["java"]),
        )

        resp = await mock_client.post(
            "/repos",
            files=_ZIP_FILE,
            data={"name": "my-repo", "languages": ["cobol"]},
        )

        assert resp.status_code == 422
        assert "cobol" in resp.json()["detail"]

    @pytest.mark.parametrize(
        ("files", "data"),
        [
            pytest.param(None, {"name": "my-repo"}, id="neither-source"),
            pytest.param(
                _ZIP_FILE,
                {"repo_url": _GIT_URL, "name": "my-repo"},
                id="both-sources",
            ),
            pytest.param(
                _ZIP_FILE,
                {"branch": "main", "name": "my-repo"},
                id="git-fields-without-url",
            ),
            pytest.param(
                _ZIP_FILE,
                {"token": "glpat-secret", "name": "my-repo"},
                id="token-without-url",
            ),
            pytest.param(
                None,
                {"repo_url": _GIT_URL, "name": "my-repo", "languages": ["java"]},
                id="git-with-languages",
            ),
        ],
    )
    async def test_create_repo_bad_request_returns_400(
        self,
        mock_client: httpx.AsyncClient,
        files: dict[str, Any] | None,
        data: dict[str, Any],
    ) -> None:
        """Invalid create-request combinations return 400."""
        resp = await mock_client.post("/repos", files=files, data=data)

        assert resp.status_code == 400

    @pytest.mark.parametrize(
        ("status", "joined", "expected_http"),
        [
            pytest.param(PipelineStatus.PENDING, False, 202, id="new-analysis"),
            pytest.param(PipelineStatus.COMPLETED, True, 200, id="join-existing"),
            pytest.param(
                PipelineStatus.PENDING, True, 200, id="join-restarted-failure"
            ),
        ],
    )
    async def test_create_repo_git_returns_status(
        self,
        mock_client: httpx.AsyncClient,
        repo_id: PydanticObjectId,
        mock_create_repo: AsyncMock,
        status: PipelineStatus,
        joined: bool,
        expected_http: int,
    ) -> None:
        """A git analysis returns 202 (new) or 200 (joined) with its current status."""
        mock_create_repo.return_value = (repo_id, status, joined)

        resp = await mock_client.post(
            "/repos",
            data={"repo_url": _GIT_URL, "name": "my-repo"},
        )

        assert resp.status_code == expected_http
        data = resp.json()
        assert data["repo_id"] == str(repo_id)
        assert data["status"] == status.value


class TestGetRepoEndpoint:
    """`GET /repos/{repo_id}` — report a repository with its meta and staleness."""

    async def test_get_repo_with_meta(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_client: httpx.AsyncClient,
        repo_id: PydanticObjectId,
    ) -> None:
        """Returns repo details with meta content and stats."""
        mock_meta = MagicMock(spec=RepoMetaDocument)
        mock_meta.content = "Repository overview."
        mock_meta.stats = AnalysisStats(files_detected=3, ast_success=2)
        monkeypatch.setattr(router, "get_repo_meta", AsyncMock(return_value=mock_meta))

        resp = await mock_client.get(f"/repos/{repo_id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["repo_id"] == str(repo_id)
        assert data["content"] == "Repository overview."
        assert data["stats"]["files_detected"] == 3
        assert data["stats"]["ast_success"] == 2

    async def test_get_repo_without_meta(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_client: httpx.AsyncClient,
        repo_id: PydanticObjectId,
    ) -> None:
        """Returns repo details with content=null and stats=null when no meta exists."""
        monkeypatch.setattr(router, "get_repo_meta", AsyncMock(return_value=None))

        resp = await mock_client.get(f"/repos/{repo_id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] is None
        assert data["stats"] is None

    @pytest.mark.parametrize(
        (
            "ran_analyzer_version",
            "current_version",
            "expected_version",
            "expected_stale",
        ),
        [
            pytest.param("v1", "v1", "v1", False, id="up-to-date"),
            pytest.param("v1", "v2", "v1", True, id="newer-available"),
            pytest.param(None, "v1", None, False, id="not-stamped-yet"),
            pytest.param("v2", "v2", "v2", False, id="redeployed-mid-run"),
        ],
    )
    async def test_get_repo_reports_the_analyzer_version_and_staleness(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_client: httpx.AsyncClient,
        mock_current_version: AsyncMock,
        repo_doc: RepoDocument,
        repo_id: PydanticObjectId,
        ran_analyzer_version: str | None,
        current_version: str,
        expected_version: str | None,
        expected_stale: bool,
    ) -> None:
        """Reports the version verbatim, with `stale` derived against the worker's."""
        repo_doc.ran_analyzer_version = ran_analyzer_version
        mock_current_version.return_value = current_version
        monkeypatch.setattr(router, "get_repo_meta", AsyncMock(return_value=None))

        resp = await mock_client.get(f"/repos/{repo_id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["analyzer_version"] == expected_version
        assert data["stale"] is expected_stale


class TestGetRepoPipelineEndpoint:
    """`GET /repos/{repo_id}/pipeline` — the latest run plus every attempt."""

    async def test_get_repo_pipeline_returns_the_only_attempt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_client: httpx.AsyncClient,
        repo_id: PydanticObjectId,
    ) -> None:
        """A repository analyzed once reports that run as status and sole attempt."""
        runs = [_run(repo_id, PipelineStatus.COMPLETED)]
        monkeypatch.setattr(
            router,
            "get_pipeline_with_attempts",
            AsyncMock(return_value=(runs[-1], runs)),
        )

        resp = await mock_client.get(f"/repos/{repo_id}/pipeline")

        assert resp.status_code == 200
        data = resp.json()
        assert data["repo_id"] == str(repo_id)
        assert data["status"] == "completed"
        assert "meta" not in data  # unchanged: None is excluded from the response
        assert len(data["attempts"]) == 1
        assert data["attempts"][0]["status"] == "completed"

    async def test_get_repo_pipeline_keeps_the_failure_that_was_restarted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_client: httpx.AsyncClient,
        repo_id: PydanticObjectId,
    ) -> None:
        """A restarted repository reports the new attempt, keeping the old error."""
        runs = [
            _run(repo_id, PipelineStatus.PENDING),
            _run(repo_id, PipelineStatus.FAILED, retried=True, message="boom"),
        ]
        monkeypatch.setattr(
            router,
            "get_pipeline_with_attempts",
            AsyncMock(return_value=(runs[0], runs)),
        )

        resp = await mock_client.get(f"/repos/{repo_id}/pipeline")

        assert resp.status_code == 200
        data = resp.json()

        # status/meta keep meaning "the latest attempt"
        assert data["status"] == "pending"
        assert "meta" not in data

        current, previous = data["attempts"]
        assert current["status"] == "pending"
        assert "retried_at" not in current
        assert previous["status"] == "failed"
        assert previous["meta"]["message"] == "boom"
        assert previous["retried_at"] is not None  # marked superseded, not deleted


class TestJoinRepoEndpoint:
    """`POST /repos/{repo_id}/join` — link the caller to an existing analysis."""

    async def test_join_repo_echoes_the_derived_name(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_client: httpx.AsyncClient,
        repo_id: PydanticObjectId,
    ) -> None:
        """The response carries the name the service derived, with no request body."""
        mock_join = AsyncMock(return_value="https://gitlab.example.com/acme/api-svc")
        monkeypatch.setattr(router, "join_repo", mock_join)

        resp = await mock_client.post(f"/repos/{repo_id}/join")

        assert resp.status_code == 201
        data = resp.json()
        assert data["repo_id"] == str(repo_id)
        assert data["name"] == "https://gitlab.example.com/acme/api-svc"
        mock_join.assert_awaited_once()


class TestRelaunchRepoEndpoint:
    """`POST /repos/{repo_id}/relaunch` — re-run an analysis at the current version."""

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({"token": "glpat-secret"}, id="private-repo"),
            pytest.param({}, id="empty-body"),
            pytest.param(None, id="no-body"),
        ],
    )
    async def test_relaunch_repo_forwards_token(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_client: httpx.AsyncClient,
        repo_id: PydanticObjectId,
        body: dict[str, str] | None,
    ) -> None:
        """A relaunch returns 202 with the new repo, forwarding any supplied token."""
        mock_relaunch = AsyncMock(return_value=(repo_id, PipelineStatus.PENDING))
        monkeypatch.setattr(router, "relaunch_repo", mock_relaunch)

        resp = await mock_client.post(f"/repos/{repo_id}/relaunch", json=body)

        assert resp.status_code == 202
        data = resp.json()
        assert data["repo_id"] == str(repo_id)
        assert data["status"] == "pending"
        mock_relaunch.assert_awaited_once()
        assert mock_relaunch.await_args_list[0].args[3] == (
            body.get("token") if body else None
        )
