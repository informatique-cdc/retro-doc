"""Unit tests for repos service.

This module tests the repos service against a mongomock database,
with mocks used only where external dependencies or specific call
verification are needed.
"""

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.auth.schemas import User
from app.docs.models import AnalysisStats, FileDocumentationDocument, RepoMetaDocument
from app.graphs.models import ASTDocument, CFGDocument, DFGDocument
from app.pipeline import service as pipeline_service
from app.pipeline.config import pipeline_settings
from app.pipeline.models import PipelineMeta, PipelineRunDocument, PipelineStatus
from app.repos import service
from app.repos.models import FileDocument, RepoDocument
from app.repos.service import (
    _claim_next_run,
    _find_latest_pipeline_run,
    _find_live_git_repo,
    _find_live_git_repo_by_key,
    _find_live_zip_repo,
    _find_live_zip_repo_by_key,
    _git_blob_path,
    analyze_file,
    analyze_git,
    create_repo,
    delete_repo,
    get_file_documentation,
    get_file_graphs,
    get_file_source,
    get_files,
    get_pipeline_history,
    get_pipeline_with_attempts,
    get_repo_meta,
    get_repos,
    is_stale,
    join_repo,
    relaunch_repo,
    update_user_repo,
)
from app.users.models import UserRepoDocument

_GIT_URL = "https://github.com/octo/repo"
_GIT_COMMIT = "a" * 40
_GIT_BLOB_PATH = (
    f"git/{hashlib.sha256(_GIT_URL.encode()).hexdigest()}/{_GIT_COMMIT}/src/repo"
)

# The worker derives a repository-relative path by dropping this many leading
# segments, so both source layouts must agree on it (worker `_blob_path_to_relative`)
_SOURCE_PREFIX_DEPTH = 5


_ZIP_FILENAME = "code.zip"
_ZIP_BLOB_PATH = "user/uid/abc/src/code"
_OTHER_ZIP_BLOB_PATH = "user/other/xyz/src/code"

# The 400 details raised by `create_repo`, shared by the cases that expect them.
_NO_SINGLE_SOURCE = "Provide exactly one of a zip file or a repo_url."
_GIT_FIELDS_ON_ZIP = "branch/commit/token are only valid with a repo_url."
_LANGUAGES_ON_GIT = (
    "Language filters are not supported for git repositories: "
    "All supported languages are analyzed."
)


def _existing_git_repo(
    *, analyzer_version: str = "v1", ran_analyzer_version: str | None = None
) -> RepoDocument:
    """A persisted-shape RepoDocument matching the mocked git resolution.

    `ran_analyzer_version` is a keyword for the reason it is one on
    `_existing_zip_repo`: a row the worker stamped a different version over is
    then a single expression.
    """
    return RepoDocument(
        repo_url=_GIT_URL,
        repo_hash=_GIT_COMMIT,
        analyzer_version=analyzer_version,
        ran_analyzer_version=ran_analyzer_version,
        blob_path=_GIT_BLOB_PATH,
        languages=[],
        user_count=1,
    )


def _existing_zip_repo(
    *,
    analyzer_version: str = "v1",
    ran_analyzer_version: str | None = None,
    blob_path: str = _ZIP_BLOB_PATH,
    user_count: int = 1,
) -> RepoDocument:
    """A persisted-shape RepoDocument as an uploaded archive leaves it.

    `repo_hash` stays unset, which is what makes it a zip everywhere the service
    discriminates, and `blob_path` is its identity — the archive it was cut
    from, which every relaunch of it carries over unchanged.

    `ran_analyzer_version` and `user_count` are keywords so that a row the
    worker redeployed under, or one nobody holds any more, is a single
    expression a `pytest.param` can carry.
    """
    return RepoDocument(
        repo_url=_ZIP_FILENAME,
        analyzer_version=analyzer_version,
        ran_analyzer_version=ran_analyzer_version,
        blob_path=blob_path,
        languages=["java"],
        user_count=user_count,
    )


async def _insert_wedged_run(
    repo: RepoDocument, *, run_status: PipelineStatus = PipelineStatus.PENDING
) -> PipelineRunDocument:
    """Persist an in-flight run old enough to be past the reconcile grace period."""
    age_s = pipeline_settings.PIPELINE_RUN_RECONCILE_GRACE_PERIOD_S + 60
    run = PipelineRunDocument(
        repo_id=repo.id,
        status=run_status,
        started_at=datetime.now(UTC) - timedelta(seconds=age_s),
    )
    await run.insert()
    return run


async def _request_git_analysis(
    user: User,
    *,
    name: str = "my-repo",
    token: str | None = None,
    color: str | None = None,
) -> tuple[PydanticObjectId, PipelineStatus, bool]:
    """Request an analysis of the fixture remote at its default branch.

    Every caller resolves the same `(url, commit)` through `mock_resolve_git_ref`,
    so only the requester and what they bring with them are worth spelling out
    per test.
    """
    return await analyze_git(
        _GIT_URL,
        branch=None,
        commit=None,
        token=token,
        name=name,
        user=user,
        color=color,
    )


@pytest.fixture
def mock_analyzer_version(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch `get_analyzer_version` in the repos service to a fixed value."""
    mock = AsyncMock(return_value="v1")
    monkeypatch.setattr(service, "get_analyzer_version", mock)
    return mock


@pytest.fixture
def mock_blob_container(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch `get_container_client` to return an AsyncMock container."""
    mock_container = AsyncMock()
    monkeypatch.setattr(
        service, "get_container_client", MagicMock(return_value=mock_container)
    )
    return mock_container


@pytest.fixture
def mock_file_doc(file_id: PydanticObjectId, repo_id: PydanticObjectId) -> MagicMock:
    """A mocked `FileDocument` for unit tests."""
    file_doc = MagicMock(spec=FileDocument)
    file_doc.id = file_id
    file_doc.repo_id = repo_id
    file_doc.path = "src/Main.java"
    return file_doc


@pytest.fixture
def mock_probe_instance(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch the Durable Functions instance probe the reconcile relies on."""
    mock = AsyncMock()
    monkeypatch.setattr(pipeline_service, "_probe_instance", mock)
    return mock


@pytest.fixture
def mock_repo_doc(repo_id: PydanticObjectId, blob_path: str) -> MagicMock:
    """A mocked `RepoDocument` for unit tests."""
    repo = MagicMock(spec=RepoDocument)
    repo.id = repo_id
    repo.repo_url = "code.zip"
    repo.repo_hash = None
    repo.analyzer_version = "v1"
    repo.blob_path = blob_path
    repo.languages = ["java"]
    repo.user_count = 1
    repo.created_at = datetime(2025, 1, 1, tzinfo=UTC)
    repo.updated_at = datetime(2025, 1, 1, tzinfo=UTC)
    repo.update = AsyncMock()
    return repo


@pytest.fixture
def mock_resolve_git_ref(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch `resolve_git_ref` in the repos service to a fixed resolution."""
    from app.repos.git import ResolvedGitRef

    mock = AsyncMock(
        return_value=ResolvedGitRef(
            url="https://github.com/octo/repo", commit="a" * 40, ref="main"
        )
    )
    monkeypatch.setattr(service, "resolve_git_ref", mock)
    return mock


@pytest.fixture
def mock_start_orchestration(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch `start_orchestration` to a no-op AsyncMock."""
    mock = AsyncMock()
    monkeypatch.setattr(service, "start_orchestration", mock)
    return mock


@pytest.fixture
async def persisted_user_repo_doc(
    user_repo_doc: UserRepoDocument,
) -> UserRepoDocument:
    """A persisted `UserRepoDocument`."""
    await user_repo_doc.insert()
    return user_repo_doc


@pytest.fixture
async def repo_at_current_version() -> RepoDocument:
    """A git repository already analyzed at the version the worker reports.

    "Current" is `mock_analyzer_version`'s `"v1"`, so a relaunch of this row
    lands on the run-status branch rather than creating a new repository. No
    holder is linked: tests that need one add it, or pair this with the
    unpersisted `user_repo_doc`.
    """
    repo = _existing_git_repo(analyzer_version="v1")
    await repo.insert()
    return repo


class TestAnalyzeFile:
    """`analyze_file` — start an analysis from an uploaded zip archive."""

    async def test_analyze_file_creates_all_documents(
        self,
        user: User,
        mock_blob_container: AsyncMock,
        mock_start_orchestration: AsyncMock,
        mock_analyzer_version: AsyncMock,
    ) -> None:
        """Creates RepoDocument, UserRepoDocument, and PipelineRunDocument in the database."""
        repo_id = await analyze_file(
            "code.zip", MagicMock(), "my-repo", ["java"], user, color="#FF5733"
        )

        repo = await RepoDocument.get(repo_id)
        assert repo is not None
        assert repo.languages == ["java"]
        assert repo.analyzer_version == "v1"  # provenance stamped
        assert repo.repo_hash is None  # zip repos are not deduplicated
        assert len(repo.blob_path.split("/")) == _SOURCE_PREFIX_DEPTH

        user_repos = await UserRepoDocument.find(
            UserRepoDocument.repo_id == repo_id
        ).to_list()
        assert len(user_repos) == 1
        assert user_repos[0].name == "my-repo"
        assert user_repos[0].color == "#FF5733"

        pipeline_runs = await PipelineRunDocument.find(
            PipelineRunDocument.repo_id == repo_id
        ).to_list()
        assert len(pipeline_runs) == 1

        mock_blob_container.upload_blob.assert_awaited_once()
        mock_start_orchestration.assert_awaited_once()

    async def test_analyze_file_rejects_empty_filename(self, user: User) -> None:
        """Empty filename is rejected with HTTP 400."""
        with pytest.raises(HTTPException) as exc_info:
            await analyze_file("", MagicMock(), "my-repo", ["java"], user)

        assert exc_info.value.status_code == 400

    async def test_analyze_file_rejects_non_zip(self, user: User) -> None:
        """Non-zip filenames are rejected with HTTP 400."""
        with pytest.raises(HTTPException) as exc_info:
            await analyze_file("readme.txt", MagicMock(), "my-repo", ["java"], user)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Only .zip files are accepted."


class TestAnalyzeGit:
    """`analyze_git` — start or join an analysis of a git remote at a commit."""

    async def test_analyze_git_creates_all_documents(
        self,
        user: User,
        mock_resolve_git_ref: AsyncMock,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A new git repo creates all docs, analyzes all languages, and starts a run."""
        repo_id, status, joined = await _request_git_analysis(user, color="#FF5733")

        assert joined is False
        assert status == PipelineStatus.PENDING

        repo = await RepoDocument.get(repo_id)
        assert repo is not None
        assert repo.repo_url == _GIT_URL
        assert repo.repo_hash == _GIT_COMMIT
        assert repo.analyzer_version == "v1"
        assert repo.languages == []  # git always analyzes all languages

        # Addressed by url + commit only, so every analyzer version shares one clone
        assert repo.blob_path == _GIT_BLOB_PATH

        user_repos = await UserRepoDocument.find(
            UserRepoDocument.repo_id == repo_id
        ).to_list()
        assert len(user_repos) == 1
        assert user_repos[0].name == "my-repo"
        assert user_repos[0].color == "#FF5733"

        pipeline_runs = await PipelineRunDocument.find(
            PipelineRunDocument.repo_id == repo_id
        ).to_list()
        assert len(pipeline_runs) == 1

        mock_start_orchestration.assert_awaited_once()
        args, kwargs = mock_start_orchestration.await_args
        assert args[0] == repo.blob_path
        assert args[1] == []
        assert kwargs["repo_url"] == _GIT_URL
        assert kwargs["commit"] == _GIT_COMMIT
        assert kwargs["ref"] == "main"

    async def test_analyze_git_never_reconciles(
        self,
        user: User,
        user_alt: User,
        repo_at_current_version: RepoDocument,
        mock_resolve_git_ref: AsyncMock,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
        mock_probe_instance: AsyncMock,
    ) -> None:
        """Joining reports the run as it stands; only a relaunch adjudicates it.

        A join means "show me what you see", so it inherits an in-flight attempt
        rather than deciding whether the worker still holds it.
        """
        mock_probe_instance.return_value = "NotFound"

        repo = repo_at_current_version
        await UserRepoDocument(user_id=user.uid, repo_id=repo.id, name="orig").insert()
        wedged = await _insert_wedged_run(repo)

        repo_id, status, joined = await _request_git_analysis(user_alt, name="my-name")

        assert joined is True
        assert repo_id == repo.id
        assert status == PipelineStatus.PENDING
        mock_probe_instance.assert_not_awaited()

        refreshed = await PipelineRunDocument.get(wedged.id)
        assert refreshed is not None
        assert refreshed.status == PipelineStatus.PENDING
        mock_start_orchestration.assert_not_awaited()

    @pytest.mark.parametrize(
        "existing_status",
        [
            pytest.param(PipelineStatus.PENDING, id="pending"),
            pytest.param(PipelineStatus.RUNNING, id="running"),
            pytest.param(PipelineStatus.COMPLETED, id="completed"),
        ],
    )
    async def test_analyze_git_join_reports_a_healthy_run_as_is(
        self,
        user: User,
        user_alt: User,
        repo_at_current_version: RepoDocument,
        mock_resolve_git_ref: AsyncMock,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
        existing_status: PipelineStatus,
    ) -> None:
        """Only a failure is restarted: in-flight and completed analyses are reported.

        A second user requesting the same (url, commit, version) is linked to the
        analysis that exists rather than paying for a duplicate one.
        """
        repo = repo_at_current_version
        await UserRepoDocument(user_id=user.uid, repo_id=repo.id, name="orig").insert()
        await PipelineRunDocument(repo_id=repo.id, status=existing_status).insert()

        repo_id, pipeline_status, joined = await _request_git_analysis(
            user_alt, name="my-name"
        )

        assert (repo_id, pipeline_status, joined) == (repo.id, existing_status, True)
        assert len(await PipelineRunDocument.find_all().to_list()) == 1

        refreshed = await RepoDocument.get(repo.id)
        assert refreshed is not None
        assert refreshed.user_count == 2

        joiner_link = await UserRepoDocument.find_one(
            UserRepoDocument.user_id == user_alt.uid,
            UserRepoDocument.repo_id == repo.id,
        )
        assert joiner_link is not None
        assert joiner_link.name == "my-name"

        mock_start_orchestration.assert_not_awaited()

    async def test_analyze_git_join_before_the_run_lands_reports_pending(
        self,
        user: User,
        user_alt: User,
        repo_at_current_version: RepoDocument,
        mock_resolve_git_ref: AsyncMock,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """Joining a repository whose creator has not inserted its run yet is not a 404.

        The creator links itself before inserting the run, so the joiner can see a
        repository with none. It reports the status the creator is about to write
        rather than manufacturing the run itself, which would take the claim the
        creator is about to need and leave the dispatch to nobody.
        """
        repo = repo_at_current_version
        await UserRepoDocument(user_id=user.uid, repo_id=repo.id, name="orig").insert()

        repo_id, pipeline_status, joined = await _request_git_analysis(
            user_alt, name="my-name"
        )

        assert (repo_id, pipeline_status, joined) == (
            repo.id,
            PipelineStatus.PENDING,
            True,
        )
        assert await PipelineRunDocument.find_all().to_list() == []
        mock_start_orchestration.assert_not_awaited()

    async def test_analyze_git_join_restarts_a_failed_analysis(
        self,
        user: User,
        user_alt: User,
        repo_at_current_version: RepoDocument,
        mock_resolve_git_ref: AsyncMock,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A create that lands on a failed analysis restarts it rather than serving it.

        A create asks for an analysis, and someone else's failure is not one. It is
        also not cacheable: failures here are transient far more often than they are
        a property of the commit, and this caller brought a token of their own.
        """
        repo = repo_at_current_version
        await UserRepoDocument(user_id=user.uid, repo_id=repo.id, name="orig").insert()
        failed = PipelineRunDocument(
            repo_id=repo.id,
            status=PipelineStatus.FAILED,
            finished_at=datetime(2025, 1, 1, tzinfo=UTC),
            meta=PipelineMeta(message="clone timed out", step="launch"),
        )
        await failed.insert()

        repo_id, pipeline_status, joined = await _request_git_analysis(
            user_alt, name="my-name", token="glpat-b"
        )

        assert (repo_id, pipeline_status, joined) == (
            repo.id,
            PipelineStatus.PENDING,
            True,
        )

        # Restarted in place: the failed row owns the (url, commit, version) key
        assert [r.id for r in await RepoDocument.find_all().to_list()] == [repo.id]
        refreshed = await RepoDocument.get(repo.id)
        assert refreshed is not None
        assert refreshed.user_count == 2

        joiner_link = await UserRepoDocument.find_one(
            UserRepoDocument.user_id == user_alt.uid,
            UserRepoDocument.repo_id == repo.id,
        )
        assert joiner_link is not None
        assert joiner_link.name == "my-name"

        runs = (
            await PipelineRunDocument.find(PipelineRunDocument.repo_id == repo.id)
            .sort("+_id")
            .to_list()
        )
        assert len(runs) == 2

        # The attempt that failed survives: whoever shares this repo can still read why
        assert runs[0].id == failed.id
        assert runs[0].status == PipelineStatus.FAILED
        assert runs[0].meta is not None
        assert runs[0].meta.message == "clone timed out"
        assert runs[0].retried_at is not None

        assert runs[1].status == PipelineStatus.PENDING
        assert runs[1].meta is None

        # The latest run is the new attempt, not the superseded one
        current = await _find_latest_pipeline_run(repo.id)  # type: ignore[arg-type]
        assert current is not None
        assert current.id == runs[1].id
        assert current.status == PipelineStatus.PENDING

        mock_start_orchestration.assert_awaited_once()
        args, kwargs = mock_start_orchestration.await_args
        assert args[0] == _GIT_BLOB_PATH
        assert args[2].id == runs[1].id
        assert kwargs["repo_url"] == _GIT_URL
        assert kwargs["commit"] == _GIT_COMMIT
        assert (
            kwargs["ref"] == "main"
        )  # the joiner just resolved it; don't lose the hint
        assert (
            kwargs["token"] == "glpat-b"
        )  # the joiner's own token, not the one that failed

    async def test_analyze_git_concurrent_joins_restart_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        user_alt: User,
        repo_at_current_version: RepoDocument,
        mock_resolve_git_ref: AsyncMock,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """Two creates landing on the same failed analysis start one orchestration.

        The loser's insert collides with the winner's on the unique index and it
        reports the restart already under way instead of spawning a second attempt.
        """
        repo = repo_at_current_version
        failed = PipelineRunDocument(repo_id=repo.id, status=PipelineStatus.FAILED)
        await failed.insert()

        # Both readers see the failure before either claim lands
        monkeypatch.setattr(
            service, "_find_latest_pipeline_run", AsyncMock(return_value=failed)
        )

        first, second = await asyncio.gather(
            _request_git_analysis(user, name="my-name"),
            _request_git_analysis(user_alt, name="my-name"),
        )

        assert first == (repo.id, PipelineStatus.PENDING, True)
        assert second == (repo.id, PipelineStatus.PENDING, True)

        assert (
            len(await PipelineRunDocument.find_all().to_list()) == 2
        )  # failed + one retry
        mock_start_orchestration.assert_awaited_once()

        # The winner's claim is the run itself, so the failure is stamped once
        superseded = await PipelineRunDocument.get(failed.id)
        assert superseded is not None
        assert superseded.retried_at is not None

    async def test_analyze_git_different_version_creates_new(
        self,
        user: User,
        mock_resolve_git_ref: AsyncMock,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A worker version change misses the cache and starts a fresh analysis."""
        stale = _existing_git_repo(analyzer_version="v0")  # older analyzer version
        await stale.insert()

        repo_id, _status, joined = await _request_git_analysis(user)

        assert joined is False
        assert repo_id != stale.id
        mock_start_orchestration.assert_awaited_once()

    async def test_analyze_git_already_owned_conflict(
        self,
        user: User,
        repo_at_current_version: RepoDocument,
        mock_resolve_git_ref: AsyncMock,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """Re-requesting a repo the caller already owns raises HTTP 409, without drift.

        The slot is claimed before the link is written, so the rejected link leaves
        an increment to undo.
        """
        repo = repo_at_current_version
        await UserRepoDocument(user_id=user.uid, repo_id=repo.id, name="mine").insert()

        with pytest.raises(HTTPException) as exc_info:
            await _request_git_analysis(user, name="again")

        assert exc_info.value.status_code == 409

        refreshed = await RepoDocument.get(repo.id)
        assert refreshed is not None
        assert refreshed.user_count == 1

    async def test_analyze_git_race_duplicate_key_joins(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user_alt: User,
        mock_resolve_git_ref: AsyncMock,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A lost insert race re-queries and joins the winning analysis."""
        winner = _existing_git_repo()
        await winner.insert()
        await PipelineRunDocument(
            repo_id=winner.id, status=PipelineStatus.RUNNING
        ).insert()

        # First dedup lookup misses (pre-race); the re-query after the failed insert
        # finds the winner.
        monkeypatch.setattr(
            service, "_find_live_git_repo", AsyncMock(side_effect=[None, winner])
        )
        monkeypatch.setattr(
            RepoDocument,
            "insert",
            AsyncMock(side_effect=DuplicateKeyError("E11000 duplicate key")),
        )

        repo_id, status, joined = await _request_git_analysis(user_alt, name="racer")

        assert joined is True
        assert repo_id == winner.id
        assert status == PipelineStatus.RUNNING
        mock_start_orchestration.assert_not_awaited()

    @pytest.mark.parametrize(
        "token",
        [
            pytest.param("glpat-secret", id="private-repo"),
            pytest.param(None, id="public-repo"),
        ],
    )
    async def test_analyze_git_forwards_token(
        self,
        user: User,
        token: str | None,
        mock_resolve_git_ref: AsyncMock,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """The PAT — or its absence — reaches both the accessibility probe and the worker."""
        await _request_git_analysis(user, token=token)

        _, resolve_kwargs = mock_resolve_git_ref.await_args
        _, start_kwargs = mock_start_orchestration.await_args
        assert resolve_kwargs["token"] == token
        assert start_kwargs["token"] == token

    async def test_analyze_git_inaccessible_creates_nothing(
        self,
        user: User,
        mock_resolve_git_ref: AsyncMock,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """An unreachable remote aborts before any document is written.

        Guards the ordering in `analyze_git`: the accessibility probe must stay
        ahead of the first insert, so a failed probe can never leave a half-created
        repository behind.
        """
        mock_resolve_git_ref.side_effect = HTTPException(status_code=422)

        with pytest.raises(HTTPException) as exc_info:
            await _request_git_analysis(user, token="bad-token")

        assert exc_info.value.status_code == 422
        assert await RepoDocument.find_all().to_list() == []
        assert await UserRepoDocument.find_all().to_list() == []
        assert await PipelineRunDocument.find_all().to_list() == []
        mock_start_orchestration.assert_not_awaited()

    async def test_analyze_git_inaccessible_cannot_restart_an_existing_failure(
        self,
        user: User,
        user_alt: User,
        repo_at_current_version: RepoDocument,
        mock_resolve_git_ref: AsyncMock,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """Restarting a failed analysis is unreachable without access to the remote.

        A caller who cannot read the repository must not join it, restart it,
        or learn that it exists.
        """
        repo = repo_at_current_version
        await UserRepoDocument(user_id=user.uid, repo_id=repo.id, name="orig").insert()
        await PipelineRunDocument(
            repo_id=repo.id, status=PipelineStatus.FAILED
        ).insert()

        mock_resolve_git_ref.side_effect = HTTPException(status_code=422)

        with pytest.raises(HTTPException) as exc_info:
            await _request_git_analysis(user_alt, token="bad-token")

        assert exc_info.value.status_code == 422

        refreshed = await RepoDocument.get(repo.id)
        assert refreshed is not None
        assert refreshed.user_count == 1  # no link for the caller without access

        links = await UserRepoDocument.find_all().to_list()
        assert [link.user_id for link in links] == [user.uid]

        runs = await PipelineRunDocument.find_all().to_list()
        assert len(runs) == 1
        assert runs[0].status == PipelineStatus.FAILED
        assert runs[0].retried_at is None  # nothing claimed it

        mock_start_orchestration.assert_not_awaited()


class TestClaimNextRun:
    """`_claim_next_run` — take a repository's single in-flight run slot."""

    async def test_claim_next_run_creates_a_pending_run(
        self,
        persisted_repo_doc: RepoDocument,
    ) -> None:
        """An unclaimed repository yields a pending run the caller owns."""
        claimed = await _claim_next_run(persisted_repo_doc.id)  # type: ignore[arg-type]

        assert claimed is not None
        assert claimed.repo_id == persisted_repo_doc.id
        assert claimed.status == PipelineStatus.PENDING

        stored = await PipelineRunDocument.find_all().to_list()
        assert [run.id for run in stored] == [claimed.id]

    @pytest.mark.parametrize(
        "in_flight",
        [
            pytest.param(PipelineStatus.PENDING, id="pending"),
            pytest.param(PipelineStatus.RUNNING, id="running"),
        ],
    )
    async def test_claim_next_run_is_none_while_one_is_in_flight(
        self,
        persisted_repo_doc: RepoDocument,
        in_flight: PipelineStatus,
    ) -> None:
        """A run already in flight holds the claim, and losing it is not an error.

        The store arbitrates via the partial unique index, so the loser gets a
        `DuplicateKeyError` that `_claim_next_run` reports as None rather than
        surfacing as a 500 under exactly the concurrency it exists to handle.
        """
        holder = PipelineRunDocument(repo_id=persisted_repo_doc.id, status=in_flight)
        await holder.insert()

        assert await _claim_next_run(persisted_repo_doc.id) is None  # type: ignore[arg-type]
        assert [run.id for run in await PipelineRunDocument.find_all().to_list()] == [
            holder.id
        ]

    @pytest.mark.parametrize(
        "terminal",
        [
            pytest.param(PipelineStatus.FAILED, id="failed"),
            pytest.param(PipelineStatus.COMPLETED, id="completed"),
        ],
    )
    async def test_claim_next_run_is_free_once_the_previous_attempt_is_terminal(
        self,
        persisted_repo_doc: RepoDocument,
        terminal: PipelineStatus,
    ) -> None:
        """Terminal attempts stay readable and do not hold the claim.

        The history keeps one document per attempt, so the index must constrain
        only the non-terminal statuses.
        """
        previous = PipelineRunDocument(repo_id=persisted_repo_doc.id, status=terminal)
        await previous.insert()

        claimed = await _claim_next_run(persisted_repo_doc.id)  # type: ignore[arg-type]

        assert claimed is not None
        assert {run.id for run in await PipelineRunDocument.find_all().to_list()} == {
            previous.id,
            claimed.id,
        }

    async def test_claim_next_run_is_scoped_to_the_repository(
        self,
        persisted_repo_doc: RepoDocument,
    ) -> None:
        """Another repository's pending run does not block this one."""
        other = RepoDocument(repo_url="other", blob_path="other", analyzer_version="v1")
        await other.insert()
        await PipelineRunDocument(repo_id=other.id).insert()

        assert await _claim_next_run(persisted_repo_doc.id) is not None  # type: ignore[arg-type]


class TestCreateRepo:
    """`create_repo` — validate a create request and dispatch it to the right source."""

    @pytest.fixture
    def mock_validate_languages(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        """Patch `validate_languages` in the repos service to a no-op."""
        mock = AsyncMock()
        monkeypatch.setattr(service, "validate_languages", mock)
        return mock

    async def test_create_repo_dispatches_to_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        repo_id: PydanticObjectId,
        mock_validate_languages: AsyncMock,
    ) -> None:
        """A zip upload dispatches to analyze_file and reports a pending, unjoined run."""
        file_data = MagicMock()
        mock_analyze_file = AsyncMock(return_value=repo_id)
        monkeypatch.setattr(service, "analyze_file", mock_analyze_file)

        result = await create_repo(
            filename="code.zip",
            file_data=file_data,
            repo_url=None,
            branch=None,
            commit=None,
            token=None,
            name="my-repo",
            languages=["java"],
            user=user,
            color="#FF5733",
        )

        assert result == (repo_id, PipelineStatus.PENDING, False)
        mock_validate_languages.assert_awaited_once_with(["java"])
        mock_analyze_file.assert_awaited_once_with(
            filename="code.zip",
            file_data=file_data,
            name="my-repo",
            languages=["java"],
            user=user,
            color="#FF5733",
        )

    async def test_create_repo_dispatches_to_git(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        repo_id: PydanticObjectId,
    ) -> None:
        """A repo_url dispatches to analyze_git and forwards its result unchanged."""
        git_result = (repo_id, PipelineStatus.COMPLETED, True)
        mock_analyze_git = AsyncMock(return_value=git_result)
        monkeypatch.setattr(service, "analyze_git", mock_analyze_git)

        result = await create_repo(
            filename=None,
            file_data=None,
            repo_url=_GIT_URL,
            branch="main",
            commit=None,
            token="glpat-secret",
            name="my-repo",
            languages=[],
            user=user,
            color=None,
        )

        assert result == (repo_id, PipelineStatus.COMPLETED, True)
        mock_analyze_git.assert_awaited_once_with(
            repo_url=_GIT_URL,
            branch="main",
            commit=None,
            token="glpat-secret",
            name="my-repo",
            user=user,
            color=None,
        )

    @pytest.mark.parametrize(
        ("overrides", "expected_detail"),
        [
            pytest.param({}, _NO_SINGLE_SOURCE, id="neither-source"),
            pytest.param(
                {
                    "filename": "code.zip",
                    "file_data": MagicMock(),
                    "repo_url": _GIT_URL,
                },
                _NO_SINGLE_SOURCE,
                id="both-sources",
            ),
            pytest.param(
                {"filename": "code.zip", "file_data": MagicMock(), "branch": "main"},
                _GIT_FIELDS_ON_ZIP,
                id="zip-with-git-fields",
            ),
            pytest.param(
                {
                    "filename": "code.zip",
                    "file_data": MagicMock(),
                    "token": "glpat-secret",
                },
                _GIT_FIELDS_ON_ZIP,
                id="zip-with-token",
            ),
            pytest.param(
                {"repo_url": _GIT_URL, "languages": ["java"]},
                _LANGUAGES_ON_GIT,
                id="git-with-languages",
            ),
            pytest.param(
                {"file_data": MagicMock()},
                "Filename is required",
                id="missing-filename",
            ),
        ],
    )
    async def test_create_repo_bad_request_returns_400(
        self,
        user: User,
        mock_validate_languages: AsyncMock,
        overrides: dict[str, Any],
        expected_detail: str,
    ) -> None:
        """Invalid create-request combinations raise HTTP 400 with a specific message.

        Every case must also leave `validate_languages` unawaited: each branch bails
        out before reaching it, so no rejected request ever costs a worker call. That
        is what keeps a bad git request answering the truthful 400 rather than the
        misleading 422 the old request-level dependency produced.
        """
        base_request: dict[str, Any] = dict(
            filename=None,
            file_data=None,
            repo_url=None,
            branch=None,
            commit=None,
            token=None,
            name="my-repo",
            languages=[],
            user=user,
            color=None,
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_repo(**{**base_request, **overrides})

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == expected_detail
        mock_validate_languages.assert_not_awaited()


class TestDeleteRepo:
    """`delete_repo` — drop a user's link to a repository and release its slot."""

    async def test_delete_repo_decrements_count(
        self,
        persisted_repo_doc: RepoDocument,
        persisted_user_repo_doc: UserRepoDocument,
    ) -> None:
        """Deletes children, then UserRepoDocument, then decrements user_count."""
        await delete_repo(persisted_user_repo_doc, persisted_repo_doc)

        refreshed = await RepoDocument.get(persisted_repo_doc.id)
        assert refreshed is not None
        assert refreshed.user_count == 0

        user_repo = await UserRepoDocument.get(persisted_user_repo_doc.id)
        assert user_repo is None


class TestFindLatestPipelineRun:
    """`_find_latest_pipeline_run` — a repository's most recent attempt, or `None`."""

    async def test_find_latest_pipeline_run_returns_latest(
        self,
        persisted_repo_doc: RepoDocument,
    ) -> None:
        """Returns the most recent PipelineRunDocument for the repository."""
        superseded = PipelineRunDocument(
            repo_id=persisted_repo_doc.id, status=PipelineStatus.FAILED
        )
        await superseded.insert()
        run = PipelineRunDocument(repo_id=persisted_repo_doc.id)
        await run.insert()

        result = await _find_latest_pipeline_run(persisted_repo_doc.id)  # type: ignore[arg-type]

        assert result is not None
        assert result.id == run.id

    async def test_find_latest_pipeline_run_is_none_when_not_found(
        self,
        repo_id: PydanticObjectId,
    ) -> None:
        """Returns None rather than raising: no run yet is not a missing repository."""
        assert await _find_latest_pipeline_run(repo_id) is None


class TestFindLiveGitRepo:
    """`_find_live_git_repo` — resolve a live git repository by its version floor."""

    @pytest.mark.parametrize(
        ("analyzer_version", "ran_analyzer_version", "floor"),
        [
            pytest.param("v1", None, "v1", id="not-stamped-yet"),
            pytest.param("v1", "v1", "v1", id="stamped-cleanly"),
            pytest.param("v1", "v2", "v2", id="worker-redeployed-mid-run"),
        ],
    )
    async def test_find_live_git_repo_matches_the_version_floor(
        self,
        analyzer_version: str,
        ran_analyzer_version: str | None,
        floor: str,
    ) -> None:
        """Dedup resolves a row by what it produced, not by the label it was created with."""
        repo = _existing_git_repo(analyzer_version=analyzer_version)
        repo.ran_analyzer_version = ran_analyzer_version
        await repo.insert()

        found = await _find_live_git_repo(_GIT_URL, _GIT_COMMIT, floor)
        assert found is not None
        assert found.id == repo.id

        other = "v9"
        assert await _find_live_git_repo(_GIT_URL, _GIT_COMMIT, other) is None

    async def test_find_live_git_repo_prefers_the_honestly_keyed_row(self) -> None:
        """When a drifted and a cleanly-keyed row both hold v2, the lookup is stable."""
        drifted = _existing_git_repo(analyzer_version="v1")
        drifted.ran_analyzer_version = "v2"
        await drifted.insert()
        clean = _existing_git_repo(analyzer_version="v2")
        await clean.insert()

        # Twice: an unsorted query would pass this intermittently
        for _ in range(2):
            found = await _find_live_git_repo(_GIT_URL, _GIT_COMMIT, "v2")
            assert found is not None
            assert found.id == clean.id

    async def test_find_live_git_repo_skips_abandoned_rows(self) -> None:
        """A row nobody holds is invisible to dedup."""
        abandoned = _existing_git_repo()
        abandoned.user_count = 0
        await abandoned.insert()

        assert await _find_live_git_repo(_GIT_URL, _GIT_COMMIT, "v1") is None
        assert await _find_live_git_repo_by_key(_GIT_URL, _GIT_COMMIT, "v1") is None


class TestFindLiveGitRepoByKey:
    """`_find_live_git_repo_by_key` — resolve the holder of a git unique key."""

    async def test_find_live_git_repo_by_key_finds_a_drifted_key_owner(self) -> None:
        """Collision recovery resolves by unique key, where the floor lookup misses.

        A row whose worker redeployed mid-run still owns its `(url, commit, version)`
        key, so an insert colliding with it must be able to find it — otherwise the
        lost race turns into a 500.
        """
        repo = _existing_git_repo(analyzer_version="v2")
        repo.ran_analyzer_version = "v3"
        await repo.insert()

        assert await _find_live_git_repo(_GIT_URL, _GIT_COMMIT, "v2") is None

        found = await _find_live_git_repo_by_key(_GIT_URL, _GIT_COMMIT, "v2")
        assert found is not None
        assert found.id == repo.id


class TestFindLiveZipRepo:
    """`_find_live_zip_repo` — resolve a live uploaded archive by its version floor."""

    @pytest.mark.parametrize(
        ("analyzer_version", "ran_analyzer_version", "floor"),
        [
            pytest.param("v1", None, "v1", id="not-stamped-yet"),
            pytest.param("v1", "v1", "v1", id="stamped-cleanly"),
            pytest.param("v1", "v2", "v2", id="worker-redeployed-mid-run"),
        ],
    )
    async def test_find_live_zip_repo_matches_the_version_floor(
        self,
        analyzer_version: str,
        ran_analyzer_version: str | None,
        floor: str,
    ) -> None:
        """An upload resolves by what it produced, as a commit does.

        The `not-stamped-yet` case also pins that `Eq(repo_hash, None)` matches a
        row stored *without* the field, `keep_nulls = False` dropping it.
        """
        repo = _existing_zip_repo(
            analyzer_version=analyzer_version, ran_analyzer_version=ran_analyzer_version
        )
        await repo.insert()

        found = await _find_live_zip_repo(_ZIP_BLOB_PATH, floor)
        assert found is not None
        assert found.id == repo.id

        assert await _find_live_zip_repo(_ZIP_BLOB_PATH, "v9") is None

    @pytest.mark.parametrize(
        ("make_repo", "blob_path", "by_key_finds"),
        [
            pytest.param(
                lambda: _existing_zip_repo(blob_path=_OTHER_ZIP_BLOB_PATH),
                _ZIP_BLOB_PATH,
                False,
                id="another-upload",
            ),
            pytest.param(_existing_git_repo, _GIT_BLOB_PATH, True, id="git-row"),
            pytest.param(
                lambda: _existing_zip_repo(user_count=0),
                _ZIP_BLOB_PATH,
                False,
                id="abandoned",
            ),
        ],
    )
    async def test_find_live_zip_repo_ignores_rows_that_are_not_this_upload(
        self, make_repo: Callable[[], RepoDocument], blob_path: str, by_key_finds: bool
    ) -> None:
        """Dedup sees this upload's own live rows: not another's, a git one, or an
        abandoned one.

        `by_key_finds` is where the two lookups part. Collision recovery asks who
        owns the unique key, and that key names neither `repo_hash` nor the source
        shape, so a git row at the queried path is its honest answer and only
        `user_count` excludes a row from it.
        """
        await make_repo().insert()

        assert await _find_live_zip_repo(blob_path, "v1") is None
        assert (
            await _find_live_zip_repo_by_key(blob_path, "v1") is not None
        ) is by_key_finds

    async def test_find_live_zip_repo_prefers_the_honestly_keyed_row(self) -> None:
        """Two holders of one upload must land on the same row, not fork it."""
        drifted = _existing_zip_repo(analyzer_version="v1", ran_analyzer_version="v2")
        await drifted.insert()
        clean = _existing_zip_repo(analyzer_version="v2")
        await clean.insert()

        # Twice: an unsorted query would pass this intermittently
        for _ in range(2):
            found = await _find_live_zip_repo(_ZIP_BLOB_PATH, "v2")
            assert found is not None
            assert found.id == clean.id


class TestFindLiveZipRepoByKey:
    """`_find_live_zip_repo_by_key` — resolve the holder of an upload's unique key."""

    async def test_find_live_zip_repo_by_key_finds_a_drifted_key_owner(self) -> None:
        """Collision recovery resolves by unique key, where the floor lookup misses."""
        repo = _existing_zip_repo(analyzer_version="v2", ran_analyzer_version="v3")
        await repo.insert()

        assert await _find_live_zip_repo(_ZIP_BLOB_PATH, "v2") is None

        found = await _find_live_zip_repo_by_key(_ZIP_BLOB_PATH, "v2")
        assert found is not None
        assert found.id == repo.id


class TestGetFileDocumentation:
    """`get_file_documentation` — the documentation stored for one file."""

    async def test_get_file_documentation_found(
        self,
        persisted_file_doc: FileDocument,
    ) -> None:
        """Returns documentation when it exists."""
        doc = FileDocumentationDocument(
            repo_id=persisted_file_doc.repo_id,
            file_id=persisted_file_doc.id,
            content="Generated docs.",
        )
        await doc.insert()

        result = await get_file_documentation(
            persisted_file_doc.repo_id,
            persisted_file_doc.id,  # type: ignore[arg-type]
        )

        assert result.content == "Generated docs."

    async def test_get_file_documentation_not_found(
        self,
        repo_id: PydanticObjectId,
        file_id: PydanticObjectId,
    ) -> None:
        """Raises HTTP 404 when no documentation exists."""
        with pytest.raises(HTTPException) as exc_info:
            await get_file_documentation(repo_id, file_id)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "No documentation found for this file."


class TestGetFileGraphs:
    """`get_file_graphs` — the AST, CFG and DFG documents stored for one file."""

    async def test_get_file_graphs_returns_all_graphs(
        self,
        repo_id: PydanticObjectId,
        file_id: PydanticObjectId,
    ) -> None:
        """Returns AST, CFG list, and DFG list."""
        await ASTDocument(
            repo_id=repo_id, file_id=file_id, content={"nodes": []}
        ).insert()
        await CFGDocument(
            repo_id=repo_id, file_id=file_id, scope="main", content={"edges": []}
        ).insert()
        await DFGDocument(
            repo_id=repo_id, file_id=file_id, scope=None, content={"vars": []}
        ).insert()

        ast, cfgs, dfgs = await get_file_graphs(repo_id, file_id)

        assert ast is not None
        assert ast.content == {"nodes": []}
        assert len(cfgs) == 1
        assert cfgs[0].scope == "main"
        assert len(dfgs) == 1

    async def test_get_file_graphs_returns_none_ast_and_empty_lists(
        self,
        repo_id: PydanticObjectId,
        file_id: PydanticObjectId,
    ) -> None:
        """Returns `None` for AST and empty CFG/DFG lists when no graphs exist."""
        ast, cfgs, dfgs = await get_file_graphs(repo_id, file_id)

        assert ast is None
        assert cfgs == []
        assert dfgs == []


class TestGetFileSource:
    """`get_file_source` — download and decode one file's source from blob storage."""

    async def test_get_file_source_downloads_and_decodes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_repo_doc: MagicMock,
        mock_file_doc: MagicMock,
    ) -> None:
        """Downloads the blob and decodes it as a string."""
        mock_downloader = AsyncMock()
        mock_downloader.readall.return_value = b"public class Main {}"

        mock_container = AsyncMock()
        mock_container.download_blob.return_value = mock_downloader
        monkeypatch.setattr(
            service, "get_container_client", MagicMock(return_value=mock_container)
        )

        result = await get_file_source(mock_repo_doc, mock_file_doc)

        expected_path = f"{mock_repo_doc.blob_path}/{mock_file_doc.path}"
        mock_container.download_blob.assert_awaited_once_with(expected_path)
        assert result == "public class Main {}"


class TestGetFiles:
    """`get_files` — every file document stored for a repository."""

    async def test_get_files_returns_matching(
        self,
        persisted_file_doc: FileDocument,
        repo_id: PydanticObjectId,
    ) -> None:
        """Returns all files for the given repository."""
        result = await get_files(repo_id)

        assert len(result) == 1
        assert result[0].id == persisted_file_doc.id


class TestGetPipelineHistory:
    """`get_pipeline_history` — every attempt on a repository, newest first."""

    async def test_get_pipeline_history_returns_attempts_newest_first(
        self,
        persisted_repo_doc: RepoDocument,
    ) -> None:
        """Every attempt on the repository, the most recent one first."""
        first = PipelineRunDocument(
            repo_id=persisted_repo_doc.id,
            status=PipelineStatus.FAILED,
            retried_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        await first.insert()
        second = PipelineRunDocument(repo_id=persisted_repo_doc.id)
        await second.insert()

        # Another repository's run must not leak into this one's history
        await PipelineRunDocument(repo_id=PydanticObjectId()).insert()

        result = await get_pipeline_history(persisted_repo_doc.id)  # type: ignore[arg-type]

        assert [run.id for run in result] == [second.id, first.id]
        latest = await _find_latest_pipeline_run(persisted_repo_doc.id)  # type: ignore[arg-type]
        assert latest is not None
        assert result[0].id == latest.id

    async def test_get_pipeline_history_is_empty_when_none_exist(
        self,
        repo_id: PydanticObjectId,
    ) -> None:
        """Returns an empty list rather than raising: the 404 is the reader's call."""
        assert await get_pipeline_history(repo_id) == []


class TestGetPipelineWithAttempts:
    """`get_pipeline_with_attempts` — the latest attempt alongside the full history."""

    async def test_get_pipeline_with_attempts_returns_latest_and_every_attempt(
        self,
        persisted_repo_doc: RepoDocument,
    ) -> None:
        """The latest run comes back alongside the full history, newest first."""
        first = PipelineRunDocument(
            repo_id=persisted_repo_doc.id,
            status=PipelineStatus.FAILED,
            retried_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        await first.insert()
        second = PipelineRunDocument(repo_id=persisted_repo_doc.id)
        await second.insert()

        current, attempts = await get_pipeline_with_attempts(persisted_repo_doc.id)  # type: ignore[arg-type]

        assert current.id == second.id
        assert [run.id for run in attempts] == [second.id, first.id]
        assert attempts[0].id == current.id

    async def test_get_pipeline_with_attempts_raises_404_when_none_exist(
        self,
        repo_id: PydanticObjectId,
    ) -> None:
        """No runs is a 404: `status` has no honest value to report."""
        with pytest.raises(HTTPException) as exc_info:
            await get_pipeline_with_attempts(repo_id)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "No pipeline run found for this repository."


class TestGetRepoMeta:
    """`get_repo_meta` — a repository's own documentation document, or `None`."""

    async def test_get_repo_meta_found(self, persisted_repo_doc: RepoDocument) -> None:
        """Returns RepoMetaDocument when it exists."""
        meta = RepoMetaDocument(
            repo_id=persisted_repo_doc.id,
            content="Repository overview.",
            stats=AnalysisStats(files_detected=3, ast_success=2),
        )
        await meta.insert()

        result = await get_repo_meta(persisted_repo_doc.id)  # type: ignore[arg-type]

        assert result is not None
        assert result.content == "Repository overview."
        assert result.stats.files_detected == 3
        assert result.stats.ast_success == 2

    async def test_get_repo_meta_not_found(self, repo_id: PydanticObjectId) -> None:
        """Returns None when no meta document exists."""
        result = await get_repo_meta(repo_id)

        assert result is None


class TestGetRepos:
    """`get_repos` — the repositories a user holds, newest first and searchable."""

    @pytest.fixture
    async def two_persisted_user_repo_docs(
        self,
        user: User,
    ) -> tuple[UserRepoDocument, UserRepoDocument]:
        """Two persisted `RepoDocument`/`UserRepoDocument` pairs."""
        repo_a = RepoDocument(
            repo_url="a", blob_path="a", analyzer_version="v1", languages=["java"]
        )
        repo_b = RepoDocument(
            repo_url="b", blob_path="b", analyzer_version="v1", languages=["java"]
        )
        await repo_a.insert()
        await repo_b.insert()

        ur_a = UserRepoDocument(user_id=user.uid, repo_id=repo_a.id, name="repo-a")
        ur_b = UserRepoDocument(user_id=user.uid, repo_id=repo_b.id, name="repo-b")
        await ur_a.insert()
        await ur_b.insert()

        return ur_a, ur_b

    async def test_get_repos_joins_correctly(
        self,
        user: User,
        persisted_repo_doc: RepoDocument,
        persisted_user_repo_doc: UserRepoDocument,
    ) -> None:
        """Returns (repo, user_repo) tuples joined correctly."""
        result = await get_repos(user)

        assert len(result) == 1
        repo, user_repo = result[0]
        assert repo.id == persisted_repo_doc.id
        assert user_repo.id == persisted_user_repo_doc.id

    async def test_get_repos_returns_empty_when_no_repos(self, user: User) -> None:
        """Returns an empty list when the user has no repositories."""
        result = await get_repos(user)

        assert result == []

    async def test_get_repos_returns_newest_first(
        self,
        user: User,
        two_persisted_user_repo_docs: tuple[UserRepoDocument, UserRepoDocument],
    ) -> None:
        """Returns repos ordered by `UserRepoDocument` creation time, newest first."""
        ur_a, ur_b = two_persisted_user_repo_docs

        result = await get_repos(user)

        assert len(result) == 2
        assert result[0][1].id == ur_b.id  # newest user-repo first
        assert result[1][1].id == ur_a.id

    @pytest.mark.parametrize(
        ("search", "expected_count"),
        [
            pytest.param("repo-a", 1, id="exact"),
            pytest.param("REPO-A", 1, id="case-insensitive"),
            pytest.param("repo", 2, id="partial"),
            pytest.param("nonexistent", 0, id="no-match"),
            pytest.param(None, 2, id="no-filter"),
        ],
    )
    @pytest.mark.usefixtures("two_persisted_user_repo_docs")
    async def test_get_repos_search(
        self,
        user: User,
        search: str | None,
        expected_count: int,
    ) -> None:
        """Filters repos by case-insensitive substring match on name."""
        result = await get_repos(user, search)

        assert len(result) == expected_count

    async def test_get_repos_skips_link_without_repo(
        self,
        user: User,
        persisted_repo_doc: RepoDocument,
        persisted_user_repo_doc: UserRepoDocument,
    ) -> None:
        """A link whose repository is gone costs its own row, not the whole listing.

        The guard is cheap and the failure it prevents is total, so it is pinned
        before it can be reached.
        """
        orphan = UserRepoDocument(
            user_id=user.uid,
            repo_id=PydanticObjectId("00000000000000000000dead"),
            name="orphan",
        )
        await orphan.insert()

        result = await get_repos(user)

        assert len(result) == 1
        assert result[0][1].id == persisted_user_repo_doc.id


class TestGitBlobPath:
    """`_git_blob_path` — the blob prefix a git clone's source is written under."""

    def test_git_blob_path_is_addressed_by_url_and_commit_only(self) -> None:
        """The analyzer version is absent, so every version shares one clone."""
        assert _git_blob_path(_GIT_URL, _GIT_COMMIT) == _GIT_BLOB_PATH
        assert (
            _git_blob_path("https://github.com/octo/other", _GIT_COMMIT)
            != _GIT_BLOB_PATH
        )

    @pytest.mark.parametrize(
        "url",
        [
            pytest.param("https://github.com/octo/repo", id="plain"),
            pytest.param("https://gitlab.corp/group/sub/deep-repo", id="nested-group"),
            pytest.param("https://git.example.com", id="no-path"),
            pytest.param("https://git.example.com/octo/we!rd na me", id="unsafe-name"),
        ],
    )
    def test_git_source_path_depth(self, url: str) -> None:
        """A git source prefix is the agreed depth, whatever the URL."""
        assert len(_git_blob_path(url, _GIT_COMMIT).split("/")) == _SOURCE_PREFIX_DEPTH


class TestIsStale:
    """`is_stale` — whether a relaunch would yield newer output than a repo holds."""

    @pytest.mark.parametrize(
        ("analyzer_version", "ran_analyzer_version", "current", "expected"),
        [
            pytest.param("v1", None, "v1", False, id="running-at-current"),
            pytest.param("v1", None, "v2", True, id="running-behind"),
            pytest.param("v1", "v1", "v2", True, id="reported-behind"),
            pytest.param("v1", "v2", "v2", False, id="drifted-onto-current"),
            pytest.param("v1", "v2", "v3", True, id="drifted-but-still-behind"),
            pytest.param("v2", "v1", "v2", False, id="drift-owns-current-key"),
            pytest.param(
                "v2", "v1", "v3", True, id="drift-owns-current-key-still-behind"
            ),
            pytest.param("v2", None, "v1", False, id="ahead-after-rollback"),
            pytest.param("1.4.0", None, "v1.4.0", False, id="equivalent-spellings"),
            pytest.param("abc123", None, "abc123", False, id="opaque-identical"),
            pytest.param("abc123", None, "def456", True, id="opaque-differs"),
        ],
    )
    def test_is_stale(
        self,
        analyzer_version: str,
        ran_analyzer_version: str | None,
        current: str,
        expected: bool,
    ) -> None:
        """Staleness compares what produced the artifacts against the worker's version.

        Newer, not merely different: a worker that rolled back is behind what it
        already analyzed, and two spellings of one version are one version.
        Versions that do not parse fall back to inequality.
        """
        repo = _existing_zip_repo(
            analyzer_version=analyzer_version, ran_analyzer_version=ran_analyzer_version
        )

        assert is_stale(repo, current) is expected


class TestJoinRepo:
    """`join_repo` — link a second user to a repository someone else already holds."""

    async def test_join_repo_conflict(
        self,
        persisted_repo_doc: RepoDocument,
        persisted_user_repo_doc: UserRepoDocument,
        user: User,
    ) -> None:
        """Raises HTTP 409 when the user already has the repository.

        The slot is claimed before the link is written, so a rejected link leaves an
        increment to undo: `user_count` must come back to where it started.
        """
        before = persisted_repo_doc.user_count

        with pytest.raises(HTTPException) as exc_info:
            await join_repo(persisted_repo_doc.id, user)  # type: ignore[arg-type]

        assert exc_info.value.status_code == 409

        refreshed = await RepoDocument.get(persisted_repo_doc.id)
        assert refreshed is not None
        assert refreshed.user_count == before

    async def test_join_repo_raises_404_when_source_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_id: PydanticObjectId,
        user: User,
    ) -> None:
        """Raises HTTP 404 when no source user-repo link exists."""
        monkeypatch.setattr(UserRepoDocument, "find_one", AsyncMock(return_value=None))

        with pytest.raises(HTTPException) as exc_info:
            await join_repo(repo_id, user)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Repository not found."

    async def test_join_repo_garbage_collected_never_links(
        self,
        persisted_user_repo_doc: UserRepoDocument,
        repo_id: PydanticObjectId,
        user_alt: User,
    ) -> None:
        """Raises HTTP 404 when the repo has user_count <= 0, leaving no link behind.

        The claim is refused before the link is written, so there is nothing to roll
        back and no window in which a link outlives a repository nobody holds.
        """
        gc_repo = RepoDocument(
            id=repo_id,
            repo_url="gc.zip",
            blob_path="gc/path",
            analyzer_version="v1",
            languages=["java"],
            user_count=0,
        )
        await gc_repo.insert()

        with pytest.raises(HTTPException) as exc_info:
            await join_repo(repo_id, user_alt)

        assert exc_info.value.status_code == 404

        assert (
            await UserRepoDocument.find_one(UserRepoDocument.user_id == user_alt.uid)
        ) is None

        refreshed = await RepoDocument.get(repo_id)
        assert refreshed is not None
        assert refreshed.user_count == 0

    async def test_join_repo_success(
        self,
        persisted_repo_doc: RepoDocument,
        persisted_user_repo_doc: UserRepoDocument,
        user_alt: User,
    ) -> None:
        """Imports a repo: increments user_count and creates a new UserRepoDocument."""
        name = await join_repo(persisted_repo_doc.id, user_alt)  # type: ignore[arg-type]

        assert name == "code.zip"

        refreshed = await RepoDocument.get(persisted_repo_doc.id)
        assert refreshed is not None
        assert refreshed.user_count == 2

        new_ur = await UserRepoDocument.find_one(
            UserRepoDocument.user_id == user_alt.uid
        )
        assert new_ur is not None
        assert new_ur.name == "code.zip"

    async def test_join_repo_names_a_git_repo_after_its_url(
        self, user_alt: User
    ) -> None:
        """A git row is named after its stored URL, not its blob path."""
        repo = RepoDocument(
            repo_url="https://gitlab.example.com/acme/api-svc",
            repo_hash="a" * 40,
            blob_path="git/hash/commit/src/api-svc",
            analyzer_version="v1",
        )
        await repo.insert()

        name = await join_repo(repo.id, user_alt)  # type: ignore[arg-type]

        assert name == "https://gitlab.example.com/acme/api-svc"

    async def test_join_repo_ignores_the_source_link(
        self,
        persisted_repo_doc: RepoDocument,
        user: User,
        user_alt: User,
    ) -> None:
        """The joiner takes neither the name nor the colour of an existing link.

        Both are how a holder organises their own list, so copying either would hand
        one user's wording to another and make the result depend on which link the
        store happened to return.
        """
        source = UserRepoDocument(
            user_id=user.uid,
            repo_id=persisted_repo_doc.id,
            name="ACME - Q3 client audit",
            color="#FF5733",
        )
        await source.insert()

        name = await join_repo(persisted_repo_doc.id, user_alt)  # type: ignore[arg-type]

        assert name == "code.zip"

        new_link = await UserRepoDocument.find_one(
            UserRepoDocument.user_id == user_alt.uid
        )
        assert new_link is not None
        assert new_link.name == "code.zip"
        assert new_link.color is None


class TestRelaunchRepo:
    """`relaunch_repo` — re-run a repository's analysis at the current version."""

    @pytest.fixture
    async def repo_at_older_version(
        self, user: User
    ) -> tuple[RepoDocument, UserRepoDocument]:
        """A git repository `user` holds from before the worker moved on.

        Analyzed at `"v0"` against `mock_analyzer_version`'s current `"v1"`, so a
        relaunch of this row takes the new-version branch. The starting point of
        every relaunch that is expected to move the caller onto a fresh analysis.
        """
        repo = _existing_git_repo(analyzer_version="v0")
        await repo.insert()
        link = UserRepoDocument(user_id=user.uid, repo_id=repo.id, name="mine")
        await link.insert()
        return repo, link

    @pytest.fixture
    async def zip_repo_at_older_version(
        self,
        user: User,
    ) -> tuple[RepoDocument, UserRepoDocument]:
        """An uploaded archive `user` holds from before the worker moved on.

        The zip counterpart of `repo_at_older_version`, analyzed at `"v0"` against
        `mock_analyzer_version`'s current `"v1"`, so a relaunch of this row takes
        the new-version branch. Its `blob_path` is the upload every other row in
        such a test is a descendant of.
        """
        repo = _existing_zip_repo(analyzer_version="v0")
        await repo.insert()
        link = UserRepoDocument(user_id=user.uid, repo_id=repo.id, name="mine")
        await link.insert()
        return repo, link

    async def test_relaunch_repo_adds_a_link_without_moving_the_old_one(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A relaunch leaves the previous analysis and its link fully intact."""
        old = _existing_git_repo(analyzer_version="v0")
        await old.insert()
        old_link = UserRepoDocument(
            user_id=user.uid, repo_id=old.id, name="ACME API", color="#FF5733"
        )
        await old_link.insert()

        new_id, pipeline_status = await relaunch_repo(old, old_link, user)

        assert new_id != old.id
        assert pipeline_status == PipelineStatus.PENDING

        refreshed_old = await RepoDocument.get(old.id)
        assert refreshed_old is not None
        assert refreshed_old.user_count == 1
        assert refreshed_old.analyzer_version == "v0"
        assert await UserRepoDocument.get(old_link.id) is not None

        new_repo = await RepoDocument.get(new_id)
        assert new_repo is not None
        assert new_repo.analyzer_version == "v1"
        assert new_repo.repo_url == old.repo_url
        assert new_repo.repo_hash == old.repo_hash
        # Same source address: the clone is already there, so nothing re-clones
        assert new_repo.blob_path == old.blob_path == _GIT_BLOB_PATH
        assert new_repo.languages == old.languages
        assert new_repo.user_count == 1

        links = await UserRepoDocument.find(
            UserRepoDocument.user_id == user.uid
        ).to_list()
        assert len(links) == 2
        new_link = next(link for link in links if link.repo_id == new_id)
        assert new_link.name == "ACME API"
        assert new_link.color == "#FF5733"

        mock_start_orchestration.assert_awaited_once()
        _, kwargs = mock_start_orchestration.await_args
        assert kwargs["repo_url"] == _GIT_URL
        assert kwargs["commit"] == _GIT_COMMIT

    async def test_relaunch_repo_rebuilds_a_legacy_git_source_path(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A row created under the old versioned layout converges on the shared clone."""
        old = _existing_git_repo(analyzer_version="v0")
        old.blob_path = f"git/{'x' * 64}/{_GIT_COMMIT}/v0/src"  # pre-change layout
        await old.insert()
        old_link = UserRepoDocument(user_id=user.uid, repo_id=old.id, name="mine")
        await old_link.insert()

        new_id, _pipeline_status = await relaunch_repo(old, old_link, user)

        new_repo = await RepoDocument.get(new_id)
        assert new_repo is not None
        assert new_repo.blob_path == _GIT_BLOB_PATH
        args, _ = mock_start_orchestration.await_args
        assert args[0] == _GIT_BLOB_PATH

        refreshed_old = await RepoDocument.get(old.id)
        assert refreshed_old is not None
        assert refreshed_old.blob_path == old.blob_path  # the old source stays put

    @pytest.mark.parametrize(
        ("analyzer_version", "run_status", "expected_detail"),
        [
            pytest.param(
                "v1",
                PipelineStatus.COMPLETED,
                "Repository is already at the current analyzer version.",
                id="completed",
            ),
            pytest.param(
                "v1",
                PipelineStatus.PENDING,
                "An analysis of this repository is already in progress.",
                id="pending",
            ),
            pytest.param(
                "v1",
                PipelineStatus.RUNNING,
                "An analysis of this repository is already in progress.",
                id="running",
            ),
            pytest.param(
                "v2",
                PipelineStatus.COMPLETED,
                "Repository is already at the current analyzer version.",
                id="rolled-back-worker",
            ),
        ],
    )
    async def test_relaunch_repo_with_nothing_newer_conflicts(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        user_repo_doc: UserRepoDocument,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
        analyzer_version: str,
        run_status: PipelineStatus,
        expected_detail: str,
    ) -> None:
        """Only a failed run is retried, a healthy one is neither re-run nor duplicated.

        Nothing newer to move onto — rather than equal to the current version — is
        what sends a relaunch down the repair branch. A worker that rolled back is
        behind the repository, so `rolled-back-worker` arrives here too instead of
        creating a second entry holding older output than the caller already has.
        """
        repo = _existing_git_repo(analyzer_version=analyzer_version)
        await repo.insert()
        await PipelineRunDocument(repo_id=repo.id, status=run_status).insert()

        with pytest.raises(HTTPException) as exc_info:
            await relaunch_repo(repo, user_repo_doc, user)

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == expected_detail
        assert [r.id for r in await RepoDocument.find_all().to_list()] == [repo.id]
        mock_start_orchestration.assert_not_awaited()

    async def test_relaunch_repo_owning_the_current_key_reports_why(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A repository the worker stamped an older version over is not relaunchable.

        Its `analyzer_version` is the current one, so it owns that version's row in
        the unique indexes and no second repository can be created beside it. Judged
        on the floor alone it read stale and was sent to build one anyway, where the
        insert collided with the repository itself and the caller was handed back
        their own — reported as `Repository already added to your account.`, which
        names the link rather than the reason.
        """
        repo = _existing_git_repo(analyzer_version="v1", ran_analyzer_version="v0")
        await repo.insert()
        await PipelineRunDocument(
            repo_id=repo.id, status=PipelineStatus.COMPLETED
        ).insert()
        link = UserRepoDocument(user_id=user.uid, repo_id=repo.id, name="ACME API")
        await link.insert()

        with pytest.raises(HTTPException) as exc_info:
            await relaunch_repo(repo, link, user)

        assert exc_info.value.status_code == 409
        assert (
            exc_info.value.detail
            == "Repository is already at the current analyzer version."
        )
        assert [r.id for r in await RepoDocument.find_all().to_list()] == [repo.id]
        mock_start_orchestration.assert_not_awaited()

    async def test_relaunch_repo_retries_a_failed_run_in_place(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        user_repo_doc: UserRepoDocument,
        repo_at_current_version: RepoDocument,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A failed analysis at the current version is repaired, not walled off by a 409.

        "In place" is the repository, not the run: the attempt that failed keeps its
        status and error, and the retry is a new attempt beside it.
        """
        repo = repo_at_current_version
        run = PipelineRunDocument(
            repo_id=repo.id,
            status=PipelineStatus.FAILED,
            finished_at=datetime(2025, 1, 1, tzinfo=UTC),
            meta=PipelineMeta(message="boom", step="launch"),
        )
        await run.insert()

        repo_id, pipeline_status = await relaunch_repo(repo, user_repo_doc, user)

        assert repo_id == repo.id  # an unchanged ID is how a caller tells a retry apart
        assert pipeline_status == PipelineStatus.PENDING

        # Nothing new: the failed row already owns the (url, commit, version) key
        assert [r.id for r in await RepoDocument.find_all().to_list()] == [repo.id]
        assert await UserRepoDocument.find_all().to_list() == []

        runs = (
            await PipelineRunDocument.find(PipelineRunDocument.repo_id == repo.id)
            .sort("+_id")
            .to_list()
        )
        assert len(runs) == 2

        failed, retry = runs
        assert failed.id == run.id
        assert failed.status == PipelineStatus.FAILED  # readable, not overwritten
        assert failed.meta is not None
        assert failed.meta.message == "boom"
        assert failed.retried_at is not None  # marked superseded

        assert retry.status == PipelineStatus.PENDING
        assert retry.finished_at is None
        assert retry.retried_at is None  # itself retryable if this attempt fails too
        assert retry.meta is None  # the previous attempt's failure is not carried over

        mock_start_orchestration.assert_awaited_once()
        args, kwargs = mock_start_orchestration.await_args
        assert args[0] == _GIT_BLOB_PATH
        assert args[2].id == retry.id  # the worker updates the new attempt
        assert kwargs["repo_url"] == _GIT_URL
        assert kwargs["commit"] == _GIT_COMMIT

    async def test_relaunch_repo_repairs_a_repo_with_no_run(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        user_repo_doc: UserRepoDocument,
        repo_at_current_version: RepoDocument,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A create that died before inserting its run is repaired, not 404'd."""
        repo = repo_at_current_version

        repo_id, pipeline_status = await relaunch_repo(repo, user_repo_doc, user)

        assert repo_id == repo.id
        assert pipeline_status == PipelineStatus.PENDING

        runs = await PipelineRunDocument.find(
            PipelineRunDocument.repo_id == repo.id
        ).to_list()
        assert len(runs) == 1
        assert runs[0].status == PipelineStatus.PENDING

        mock_start_orchestration.assert_awaited_once()
        args, kwargs = mock_start_orchestration.await_args
        assert args[0] == _GIT_BLOB_PATH
        assert args[2].id == runs[0].id
        assert kwargs["repo_url"] == _GIT_URL

    async def test_relaunch_repo_repairs_a_repo_with_no_run_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        user_alt: User,
        user_repo_doc: UserRepoDocument,
        repo_at_current_version: RepoDocument,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """Two relaunches repairing the same runless repository dispatch once."""
        repo = repo_at_current_version
        alt_link = UserRepoDocument(
            user_id=user_alt.uid, repo_id=repo.id, name="theirs"
        )

        # Both see the repository as runless before either claim lands
        monkeypatch.setattr(
            service, "_find_latest_pipeline_run", AsyncMock(return_value=None)
        )

        first, second = await asyncio.gather(
            relaunch_repo(repo, user_repo_doc, user),
            relaunch_repo(repo, alt_link, user_alt),
        )

        assert first == (repo.id, PipelineStatus.PENDING)
        assert second == (repo.id, PipelineStatus.PENDING)

        assert len(await PipelineRunDocument.find_all().to_list()) == 1
        mock_start_orchestration.assert_awaited_once()

    async def test_relaunch_repo_retry_claims_the_run_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        user_repo_doc: UserRepoDocument,
        repo_at_current_version: RepoDocument,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """Losing the claim to a run already in flight reports it without dispatching.

        The claim covers RUNNING as well as PENDING, so a caller acting on a read
        that has since gone stale cannot dispatch beside an attempt the worker has
        already picked up.
        """
        repo = repo_at_current_version
        run = PipelineRunDocument(repo_id=repo.id, status=PipelineStatus.RUNNING)
        await run.insert()

        # A concurrent relaunch dispatched between this caller's read and its own
        # claim, and the worker has already picked the retry up
        stale_read = PipelineRunDocument(
            id=PydanticObjectId(), repo_id=repo.id, status=PipelineStatus.FAILED
        )

        monkeypatch.setattr(
            service, "_find_latest_pipeline_run", AsyncMock(return_value=stale_read)
        )

        repo_id, pipeline_status = await relaunch_repo(repo, user_repo_doc, user)

        assert repo_id == repo.id
        assert pipeline_status == PipelineStatus.PENDING

        # Nothing inserted beside the attempt already running
        assert [r.id for r in await PipelineRunDocument.find_all().to_list()] == [
            run.id
        ]
        mock_start_orchestration.assert_not_awaited()

    async def test_relaunch_repo_retries_an_orphaned_claim(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        user_repo_doc: UserRepoDocument,
        repo_at_current_version: RepoDocument,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A failure stamped as retried but never succeeded is not wedged.

        `retried_at` is an audit trail, not a gate: a crash between stamping it and
        inserting the successor leaves a repository whose only attempt reads FAILED
        and superseded. Since nothing is in flight, the claim is free and the relaunch
        repairs it.
        """
        repo = repo_at_current_version
        orphaned = PipelineRunDocument(
            repo_id=repo.id,
            status=PipelineStatus.FAILED,
            retried_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        await orphaned.insert()

        repo_id, pipeline_status = await relaunch_repo(repo, user_repo_doc, user)

        assert repo_id == repo.id
        assert pipeline_status == PipelineStatus.PENDING

        runs = (
            await PipelineRunDocument.find(PipelineRunDocument.repo_id == repo.id)
            .sort("+_id")
            .to_list()
        )
        assert [r.id for r in runs] == [orphaned.id, runs[1].id]
        assert runs[1].status == PipelineStatus.PENDING

        mock_start_orchestration.assert_awaited_once()
        args, _ = mock_start_orchestration.await_args
        assert args[2].id == runs[1].id

    async def test_relaunch_repo_retry_survives_a_lost_stamp(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        user_repo_doc: UserRepoDocument,
        repo_at_current_version: RepoDocument,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """Dying between claiming the retry and stamping the failure is recoverable."""
        repo = repo_at_current_version
        failed = PipelineRunDocument(repo_id=repo.id, status=PipelineStatus.FAILED)
        await failed.insert()

        with (
            pytest.MonkeyPatch.context() as patched,
            pytest.raises(ConnectionError),
        ):
            patched.setattr(
                service, "_find_latest_pipeline_run", AsyncMock(return_value=failed)
            )
            patched.setattr(
                PipelineRunDocument,
                "find_one",
                MagicMock(side_effect=ConnectionError("dropped mid-stamp")),
            )
            await relaunch_repo(repo, user_repo_doc, user)

        runs = (
            await PipelineRunDocument.find(PipelineRunDocument.repo_id == repo.id)
            .sort("+_id")
            .to_list()
        )
        assert [r.status for r in runs] == [
            PipelineStatus.FAILED,
            PipelineStatus.PENDING,
        ]
        assert runs[0].retried_at is None  # the stamp never landed

        # Not wedged: the pending attempt holds the claim, so the repository reads as
        # in progress rather than as a failure nothing will ever retry
        latest = await _find_latest_pipeline_run(repo.id)  # type: ignore[arg-type]
        assert latest is not None
        assert latest.id == runs[1].id

    async def test_relaunch_repo_retries_again_after_a_failed_dispatch(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        user_repo_doc: UserRepoDocument,
        repo_at_current_version: RepoDocument,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A dispatch that fails frees the claim for the next attempt.

        `start_orchestration` marks its own run FAILED before raising, which drops
        it out of the index — otherwise one unreachable worker would leave the
        repository holding a pending run forever and refusing every retry.
        """
        repo = repo_at_current_version
        await PipelineRunDocument(
            repo_id=repo.id, status=PipelineStatus.FAILED
        ).insert()

        async def _fail_dispatch(
            blob_path: str,
            languages: list[str],
            pipeline_run: PipelineRunDocument,
            **kwargs: str | None,
        ) -> None:
            await pipeline_run.set({PipelineRunDocument.status: PipelineStatus.FAILED})
            raise HTTPException(status_code=502, detail="Worker unreachable.")

        mock_start_orchestration.side_effect = _fail_dispatch
        with pytest.raises(HTTPException) as exc_info:
            await relaunch_repo(repo, user_repo_doc, user)
        assert exc_info.value.status_code == 502

        mock_start_orchestration.side_effect = None
        repo_id, pipeline_status = await relaunch_repo(repo, user_repo_doc, user)

        assert repo_id == repo.id
        assert pipeline_status == PipelineStatus.PENDING

        runs = (
            await PipelineRunDocument.find(PipelineRunDocument.repo_id == repo.id)
            .sort("+_id")
            .to_list()
        )
        assert [r.status for r in runs] == [
            PipelineStatus.FAILED,
            PipelineStatus.FAILED,
            PipelineStatus.PENDING,
        ]

    async def test_relaunch_repo_join_before_the_run_lands_reports_pending(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        user_alt: User,
        repo_at_older_version: tuple[RepoDocument, UserRepoDocument],
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """Joining an equivalent analysis whose run has not landed yet reports pending."""
        old, old_link = repo_at_older_version

        already = _existing_git_repo(analyzer_version="v1")
        await already.insert()
        await UserRepoDocument(
            user_id=user_alt.uid, repo_id=already.id, name="theirs"
        ).insert()

        new_id, pipeline_status = await relaunch_repo(old, old_link, user)

        assert new_id == already.id
        assert pipeline_status == PipelineStatus.PENDING
        assert await PipelineRunDocument.find_all().to_list() == []
        mock_start_orchestration.assert_not_awaited()

    async def test_relaunch_repo_joins_an_equivalent_analysis(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        user_alt: User,
        repo_at_older_version: tuple[RepoDocument, UserRepoDocument],
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A relaunch reuses the run another user already paid for; no second dispatch."""
        old, old_link = repo_at_older_version

        already = _existing_git_repo(analyzer_version="v1")
        await already.insert()
        await UserRepoDocument(
            user_id=user_alt.uid, repo_id=already.id, name="theirs"
        ).insert()
        await PipelineRunDocument(
            repo_id=already.id, status=PipelineStatus.COMPLETED
        ).insert()

        new_id, pipeline_status = await relaunch_repo(old, old_link, user)

        assert new_id == already.id
        assert pipeline_status == PipelineStatus.COMPLETED

        refreshed = await RepoDocument.get(already.id)
        assert refreshed is not None
        assert refreshed.user_count == 2

        mock_start_orchestration.assert_not_awaited()

    async def test_relaunch_repo_join_restarts_a_failed_analysis(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        user_alt: User,
        repo_at_older_version: tuple[RepoDocument, UserRepoDocument],
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A relaunch landing on someone else's failed analysis restarts it."""
        old, old_link = repo_at_older_version

        already = _existing_git_repo(analyzer_version="v1")
        await already.insert()
        await UserRepoDocument(
            user_id=user_alt.uid, repo_id=already.id, name="theirs"
        ).insert()
        failed = PipelineRunDocument(
            repo_id=already.id,
            status=PipelineStatus.FAILED,
            finished_at=datetime(2025, 1, 1, tzinfo=UTC),
            meta=PipelineMeta(message="clone timed out", step="launch"),
        )
        await failed.insert()

        new_id, pipeline_status = await relaunch_repo(
            old, old_link, user, token="glpat-mine"
        )

        assert new_id == already.id
        assert pipeline_status == PipelineStatus.PENDING

        # Joined, not duplicated: the failed row owns the (url, commit, version) key
        assert {r.id for r in await RepoDocument.find_all().to_list()} == {
            old.id,
            already.id,
        }
        refreshed = await RepoDocument.get(already.id)
        assert refreshed is not None
        assert refreshed.user_count == 2

        joiner_link = await UserRepoDocument.find_one(
            UserRepoDocument.user_id == user.uid,
            UserRepoDocument.repo_id == already.id,
        )
        assert joiner_link is not None
        assert joiner_link.name == "mine"  # the caller's own label carries over

        runs = (
            await PipelineRunDocument.find(PipelineRunDocument.repo_id == already.id)
            .sort("+_id")
            .to_list()
        )
        assert len(runs) == 2

        # The attempt that failed survives: whoever shares this repo can still read why
        assert runs[0].id == failed.id
        assert runs[0].status == PipelineStatus.FAILED
        assert runs[0].meta is not None
        assert runs[0].meta.message == "clone timed out"
        assert runs[0].retried_at is not None

        assert runs[1].status == PipelineStatus.PENDING
        assert runs[1].meta is None

        # The caller's original repository is untouched, theirs to drop by hand
        refreshed_old = await RepoDocument.get(old.id)
        assert refreshed_old is not None
        assert refreshed_old.analyzer_version == "v0"
        assert (
            await PipelineRunDocument.find(
                PipelineRunDocument.repo_id == old.id
            ).to_list()
            == []
        )

        mock_start_orchestration.assert_awaited_once()
        args, kwargs = mock_start_orchestration.await_args
        assert args[0] == _GIT_BLOB_PATH
        assert args[2].id == runs[1].id
        assert kwargs["repo_url"] == _GIT_URL
        assert kwargs["commit"] == _GIT_COMMIT
        assert kwargs["ref"] == "main"  # recovered by the access gate; don't lose it
        assert kwargs["token"] == "glpat-mine"  # the joiner's, not the one that failed

    async def test_relaunch_repo_twice_conflicts(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        repo_at_older_version: tuple[RepoDocument, UserRepoDocument],
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A second relaunch finds the analysis the caller already holds and 409s."""
        old, old_link = repo_at_older_version

        await relaunch_repo(old, old_link, user)

        with pytest.raises(HTTPException) as exc_info:
            await relaunch_repo(old, old_link, user)

        assert exc_info.value.status_code == 409
        assert len(await RepoDocument.find_all().to_list()) == 2
        mock_start_orchestration.assert_awaited_once()

    async def test_relaunch_repo_race_duplicate_key_joins(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        repo_at_older_version: tuple[RepoDocument, UserRepoDocument],
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A lost insert race joins the concurrent relaunch that won it."""
        old, old_link = repo_at_older_version

        winner = _existing_git_repo(analyzer_version="v1")
        await winner.insert()
        await PipelineRunDocument(
            repo_id=winner.id, status=PipelineStatus.RUNNING
        ).insert()

        monkeypatch.setattr(
            service, "_find_live_git_repo", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            service, "_find_live_git_repo_by_key", AsyncMock(return_value=winner)
        )
        monkeypatch.setattr(
            RepoDocument,
            "insert",
            AsyncMock(side_effect=DuplicateKeyError("E11000 duplicate key")),
        )

        new_id, pipeline_status = await relaunch_repo(old, old_link, user)

        assert new_id == winner.id
        assert pipeline_status == PipelineStatus.RUNNING
        mock_start_orchestration.assert_not_awaited()

    async def test_relaunch_repo_zip_twice_conflicts(
        self,
        user: User,
        zip_repo_at_older_version: tuple[RepoDocument, UserRepoDocument],
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A second relaunch of one upload finds the first one's analysis and 409s.

        The upload is the identity, so the row the first relaunch created is
        reachable from the row it was launched from. Without that, every click
        minted another entry analyzing the same archive.
        """
        old, old_link = zip_repo_at_older_version

        new_id, _pipeline_status = await relaunch_repo(old, old_link, user)

        with pytest.raises(HTTPException) as exc_info:
            await relaunch_repo(old, old_link, user)

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "Repository already added to your account."

        assert len(await RepoDocument.find_all().to_list()) == 2
        assert len(await UserRepoDocument.find_all().to_list()) == 2
        mock_start_orchestration.assert_awaited_once()

        # The refused join claimed a slot before failing, and compensated for it
        created = await RepoDocument.get(new_id)
        assert created is not None
        assert created.user_count == 1

    @pytest.mark.parametrize(
        ("analyzer_version", "ran_analyzer_version"),
        [
            pytest.param("v1", None, id="clean-key"),
            pytest.param("v0.5", "v1", id="drifted-key"),
        ],
    )
    async def test_relaunch_repo_zip_joins_an_equivalent_analysis(
        self,
        user: User,
        user_alt: User,
        zip_repo_at_older_version: tuple[RepoDocument, UserRepoDocument],
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
        analyzer_version: str,
        ran_analyzer_version: str | None,
    ) -> None:
        """Two holders of one upload pay for one analysis, as two of one commit do.

        `drifted-key` is a row the worker redeployed mid-run: keyed at the version
        it was dispatched at and stamped with a later one, so the unique index
        cannot see the collision — only a lookup matching the version floor can.
        Without one, the caller pays for a second analysis of the same archive at a
        version it already holds.
        """
        old, old_link = zip_repo_at_older_version

        already = _existing_zip_repo(
            analyzer_version=analyzer_version, ran_analyzer_version=ran_analyzer_version
        )
        await already.insert()
        await UserRepoDocument(
            user_id=user_alt.uid, repo_id=already.id, name="theirs"
        ).insert()
        await PipelineRunDocument(
            repo_id=already.id, status=PipelineStatus.COMPLETED
        ).insert()

        new_id, pipeline_status = await relaunch_repo(old, old_link, user)

        assert new_id == already.id
        assert pipeline_status == PipelineStatus.COMPLETED

        refreshed = await RepoDocument.get(already.id)
        assert refreshed is not None
        assert refreshed.user_count == 2

        joiner_link = await UserRepoDocument.find_one(
            UserRepoDocument.user_id == user.uid,
            UserRepoDocument.repo_id == already.id,
        )
        assert joiner_link is not None
        assert joiner_link.name == "mine"  # the caller's own label carries over

        assert {r.id for r in await RepoDocument.find_all().to_list()} == {
            old.id,
            already.id,
        }
        mock_start_orchestration.assert_not_awaited()

    async def test_relaunch_repo_zip_join_restarts_a_failed_analysis(
        self,
        user: User,
        user_alt: User,
        zip_repo_at_older_version: tuple[RepoDocument, UserRepoDocument],
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A join landing on a failed analysis restarts it, and still as a zip.

        The only path that reaches `_dispatch_run` through `_join_analysis` with an
        uploaded archive, so it is the only place the restart could describe one as
        a git source without anything noticing.
        """
        old, old_link = zip_repo_at_older_version

        already = _existing_zip_repo(analyzer_version="v1")
        await already.insert()
        await UserRepoDocument(
            user_id=user_alt.uid, repo_id=already.id, name="theirs"
        ).insert()
        await PipelineRunDocument(
            repo_id=already.id, status=PipelineStatus.FAILED
        ).insert()

        new_id, pipeline_status = await relaunch_repo(old, old_link, user)

        assert new_id == already.id
        assert pipeline_status == PipelineStatus.PENDING

        args, kwargs = mock_start_orchestration.await_args
        assert args[0] == f"{_ZIP_BLOB_PATH}.zip"
        assert args[1] == ["java"]
        assert "repo_url" not in kwargs

    async def test_relaunch_repo_zip_race_duplicate_key_joins(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        zip_repo_at_older_version: tuple[RepoDocument, UserRepoDocument],
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A lost insert race joins the winner rather than surfacing a 500.

        An upload has a unique key of its own, so losing the race is now reachable
        for a zip and needs a winner to resolve to.
        """
        old, old_link = zip_repo_at_older_version

        winner = _existing_zip_repo(analyzer_version="v1")
        await winner.insert()
        await PipelineRunDocument(
            repo_id=winner.id, status=PipelineStatus.RUNNING
        ).insert()

        monkeypatch.setattr(
            service, "_find_live_zip_repo", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            RepoDocument,
            "insert",
            AsyncMock(side_effect=DuplicateKeyError("E11000 duplicate key")),
        )

        new_id, pipeline_status = await relaunch_repo(old, old_link, user)

        assert new_id == winner.id
        assert pipeline_status == PipelineStatus.RUNNING
        mock_start_orchestration.assert_not_awaited()

    async def test_relaunch_repo_forwards_token(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        repo_at_older_version: tuple[RepoDocument, UserRepoDocument],
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A private remote's PAT reaches both the probe and the worker, unpersisted."""
        old, old_link = repo_at_older_version

        await relaunch_repo(old, old_link, user, token="glpat-secret")

        _, resolve_kwargs = mock_resolve_git_ref.await_args
        _, start_kwargs = mock_start_orchestration.await_args
        assert resolve_kwargs["token"] == "glpat-secret"
        assert resolve_kwargs["commit"] == _GIT_COMMIT
        assert start_kwargs["token"] == "glpat-secret"
        assert start_kwargs["ref"] == "main"

    async def test_relaunch_repo_inaccessible_creates_nothing(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        repo_at_older_version: tuple[RepoDocument, UserRepoDocument],
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A remote the caller cannot reach aborts before any document is written.

        Relaunching is what makes the analysis fetch the remote again, so it takes
        access to the remote.
        """
        mock_resolve_git_ref.side_effect = HTTPException(status_code=422)

        old, old_link = repo_at_older_version

        with pytest.raises(HTTPException) as exc_info:
            await relaunch_repo(old, old_link, user)

        assert exc_info.value.status_code == 422
        assert [r.id for r in await RepoDocument.find_all().to_list()] == [old.id]
        assert [link.id for link in await UserRepoDocument.find_all().to_list()] == [
            old_link.id
        ]
        assert await PipelineRunDocument.find_all().to_list() == []
        mock_start_orchestration.assert_not_awaited()

    async def test_relaunch_repo_public_remote_needs_no_token(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        repo_at_older_version: tuple[RepoDocument, UserRepoDocument],
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """A reachable remote relaunches with no token at all.

        The gate is access, not a credential: a public repository resolves without
        one, so requiring a token here would refuse a relaunch the worker could
        serve.
        """
        old, old_link = repo_at_older_version

        new_id, pipeline_status = await relaunch_repo(old, old_link, user)

        assert new_id != old.id
        assert pipeline_status == PipelineStatus.PENDING
        _, resolve_kwargs = mock_resolve_git_ref.await_args
        assert resolve_kwargs["token"] is None
        mock_start_orchestration.assert_awaited_once()
        _, start_kwargs = mock_start_orchestration.await_args
        assert start_kwargs["token"] is None

    @pytest.mark.parametrize(
        ("analyzer_version", "retried_in_place"),
        [
            pytest.param("v1", True, id="retry-in-place"),
            pytest.param("v0", False, id="new-version"),
        ],
    )
    async def test_relaunch_repo_zip_dispatches_as_a_zip(
        self,
        user: User,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
        analyzer_version: str,
        retried_in_place: bool,
    ) -> None:
        """A zip relaunch re-uses the uploaded archive and keeps its language filter."""
        old = _existing_zip_repo(analyzer_version=analyzer_version)
        await old.insert()
        old_link = UserRepoDocument(user_id=user.uid, repo_id=old.id, name="mine")
        await old_link.insert()
        await PipelineRunDocument(repo_id=old.id, status=PipelineStatus.FAILED).insert()

        repo_id, _pipeline_status = await relaunch_repo(old, old_link, user)

        assert (repo_id == old.id) is retried_in_place

        resulting = await RepoDocument.get(repo_id)
        assert resulting is not None
        assert resulting.languages == ["java"]
        assert resulting.repo_hash is None

        args, kwargs = mock_start_orchestration.await_args
        assert args[0] == f"{_ZIP_BLOB_PATH}.zip"
        assert args[1] == ["java"]
        assert "repo_url" not in kwargs

    async def test_relaunch_repo_zip_never_joins_another_upload(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        user_alt: User,
        zip_repo_at_older_version: tuple[RepoDocument, UserRepoDocument],
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
    ) -> None:
        """Zip uploads are personal: a relaunch must not reach another user's row.

        The two rows differ in `blob_path` and nothing else that could exclude
        one — same filename, same shape, and the other user's is at the current
        version — so `blob_path` is the only clause standing between them.
        """
        theirs = _existing_zip_repo(blob_path=_OTHER_ZIP_BLOB_PATH)
        await theirs.insert()
        await UserRepoDocument(
            user_id=user_alt.uid, repo_id=theirs.id, name="theirs"
        ).insert()

        mine, my_link = zip_repo_at_older_version

        mock_find = AsyncMock()
        monkeypatch.setattr(service, "_find_live_git_repo", mock_find)

        new_id, _pipeline_status = await relaunch_repo(mine, my_link, user)

        mock_find.assert_not_awaited()
        assert new_id not in (mine.id, theirs.id)

        refreshed_theirs = await RepoDocument.get(theirs.id)
        assert refreshed_theirs is not None
        assert refreshed_theirs.user_count == 1

        assert (
            await UserRepoDocument.find_one(
                UserRepoDocument.user_id == user.uid,
                UserRepoDocument.repo_id == theirs.id,
            )
            is None
        )
        mock_start_orchestration.assert_awaited_once()

    async def test_relaunch_repo_retries_a_reconciled_run(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        user_repo_doc: UserRepoDocument,
        repo_at_current_version: RepoDocument,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
        mock_probe_instance: AsyncMock,
    ) -> None:
        """A dispatch that never landed stops being a permanent 409.

        Nothing in the backend writes RUNNING or COMPLETED, so such a run would hold
        the in-flight claim forever. Settling it needs no new branch: it reads FAILED
        and falls into the retry that already existed.
        """
        mock_probe_instance.return_value = "NotFound"

        repo = repo_at_current_version
        wedged = await _insert_wedged_run(repo)

        repo_id, pipeline_status = await relaunch_repo(repo, user_repo_doc, user)

        assert repo_id == repo.id
        assert pipeline_status == PipelineStatus.PENDING

        settled = await PipelineRunDocument.get(wedged.id)
        assert settled is not None
        assert settled.status == PipelineStatus.FAILED
        assert settled.meta is not None
        assert settled.meta.step == "launch"
        assert settled.retried_at is not None  # superseded by the retry, still readable

        assert len(await PipelineRunDocument.find_all().to_list()) == 2
        mock_start_orchestration.assert_awaited_once()

    @pytest.mark.parametrize(
        "runtime_status",
        [
            pytest.param("Running", id="live"),
            pytest.param(None, id="unanswerable"),
        ],
    )
    async def test_relaunch_repo_still_conflicts_on_an_unsettled_run(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        user_repo_doc: UserRepoDocument,
        repo_at_current_version: RepoDocument,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
        mock_probe_instance: AsyncMock,
        runtime_status: str | None,
    ) -> None:
        """Only proof of death releases the claim; a live or unreachable one still 409s."""
        mock_probe_instance.return_value = runtime_status

        repo = repo_at_current_version
        wedged = await _insert_wedged_run(repo)

        with pytest.raises(HTTPException) as exc_info:
            await relaunch_repo(repo, user_repo_doc, user)

        assert exc_info.value.status_code == 409
        assert (
            exc_info.value.detail
            == "An analysis of this repository is already in progress."
        )

        refreshed = await PipelineRunDocument.get(wedged.id)
        assert refreshed is not None
        assert refreshed.status == PipelineStatus.PENDING
        mock_start_orchestration.assert_not_awaited()

    async def test_relaunch_repo_reconciles_a_zip(
        self,
        user: User,
        user_repo_doc: UserRepoDocument,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
        mock_probe_instance: AsyncMock,
    ) -> None:
        """A wedged zip is the case with no other escape.

        An upload is identified by the `blob_path` it was cut from, which nobody
        else's archive shares, so the dedup path that lets a wedged git repository
        land on a healthy analysis someone else produced has nothing to offer here.
        Reconciliation is all a zip has.
        """
        mock_probe_instance.return_value = "NotFound"

        repo = _existing_zip_repo()
        await repo.insert()
        wedged = await _insert_wedged_run(repo)

        repo_id, pipeline_status = await relaunch_repo(repo, user_repo_doc, user)

        assert repo_id == repo.id
        assert pipeline_status == PipelineStatus.PENDING

        settled = await PipelineRunDocument.get(wedged.id)
        assert settled is not None
        assert settled.status == PipelineStatus.FAILED
        mock_start_orchestration.assert_awaited_once()

    async def test_relaunch_repo_reconciles_at_a_moved_analyzer_version(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        repo_at_older_version: tuple[RepoDocument, UserRepoDocument],
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
        mock_probe_instance: AsyncMock,
    ) -> None:
        """The wedge is settled even when this request will not re-dispatch it.

        The hook sits above the version branch, so a caller who leaves for a new
        repository still frees the claim on the one they left behind.
        """
        mock_probe_instance.return_value = "NotFound"

        old, old_link = repo_at_older_version
        wedged = await _insert_wedged_run(old)

        new_id, _pipeline_status = await relaunch_repo(old, old_link, user)

        assert new_id != old.id

        settled = await PipelineRunDocument.get(wedged.id)
        assert settled is not None
        assert settled.status == PipelineStatus.FAILED
        assert settled.retried_at is None  # freed, not retried: the caller moved on

    @pytest.mark.parametrize(
        ("failing_call", "expected_status"),
        [
            pytest.param("resolve", 422, id="no-remote-access"),
            pytest.param("version", 502, id="worker-down"),
        ],
    )
    async def test_relaunch_repo_does_not_reconcile_when_the_request_aborts(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        user_repo_doc: UserRepoDocument,
        repo_at_current_version: RepoDocument,
        mock_analyzer_version: AsyncMock,
        mock_probe_instance: AsyncMock,
        failing_call: str,
        expected_status: int,
    ) -> None:
        """A request that aborts before the hook writes nothing at all.

        The access gate stays first, so an unreachable remote never reaches the
        reconcile. Neither does an unreachable worker: both calls go to the same
        base URL, so one that cannot answer the version fetch cannot answer a probe.
        """
        aborting = {"resolve": mock_resolve_git_ref, "version": mock_analyzer_version}
        aborting[failing_call].side_effect = HTTPException(status_code=expected_status)
        mock_probe_instance.return_value = "NotFound"

        repo = repo_at_current_version
        wedged = await _insert_wedged_run(repo)

        with pytest.raises(HTTPException) as exc_info:
            await relaunch_repo(repo, user_repo_doc, user)

        assert exc_info.value.status_code == expected_status
        mock_probe_instance.assert_not_awaited()

        refreshed = await PipelineRunDocument.get(wedged.id)
        assert refreshed is not None
        assert refreshed.status == PipelineStatus.PENDING

    async def test_relaunch_repo_survives_a_failing_reconcile(
        self,
        mock_resolve_git_ref: AsyncMock,
        user: User,
        user_repo_doc: UserRepoDocument,
        repo_at_current_version: RepoDocument,
        mock_analyzer_version: AsyncMock,
        mock_start_orchestration: AsyncMock,
        mock_probe_instance: AsyncMock,
    ) -> None:
        """A repair that blows up degrades to no repair, never to a 500."""
        mock_probe_instance.side_effect = RuntimeError("boom")

        repo = repo_at_current_version
        await _insert_wedged_run(repo)

        with pytest.raises(HTTPException) as exc_info:
            await relaunch_repo(repo, user_repo_doc, user)

        assert exc_info.value.status_code == 409
        mock_start_orchestration.assert_not_awaited()


class TestRepoUniqueIndexes:
    """`RepoDocument.Settings.indexes` — the two partial unique keys a repo is addressed by."""

    @pytest.mark.parametrize(
        ("position", "key", "partial_filter"),
        [
            pytest.param(
                0,
                {"repo_url": 1, "repo_hash": 1, "analyzer_version": 1},
                {"repo_hash": {"$type": "string"}, "user_count": {"$gt": 0}},
                id="git-key",
            ),
            pytest.param(
                1,
                {"blob_path": 1, "analyzer_version": 1},
                {"user_count": {"$gt": 0}},
                id="source-key",
            ),
        ],
    )
    def test_repo_unique_indexes(
        self,
        position: int,
        key: dict[str, int],
        partial_filter: dict[str, dict[str, str | int]],
    ) -> None:
        """Both unique keys apply only to rows someone still holds.

        The source key must also reach zip rows, which the git key filters out by
        requiring a `repo_hash`. Its own filter names `user_count` and nothing
        else: excluding git would take `{"$exists": false}` on `repo_hash`, which
        Cosmos DB (RU) does not admit in a `partialFilterExpression`.
        """
        index = RepoDocument.Settings.indexes[position].document

        assert index["key"] == key
        assert index["unique"] is True
        assert index["partialFilterExpression"] == partial_filter


class TestUpdateUserRepo:
    """`update_user_repo` — apply a partial update to a user's repository link."""

    @pytest.mark.parametrize(
        (
            "initial_color",
            "color",
            "name",
            "fields_set",
            "expected_color",
            "expected_name",
        ),
        [
            pytest.param(
                None, "#00ff00", None, {"color"}, "#00ff00", "test-repo", id="set-color"
            ),
            pytest.param(
                None, None, "new-name", {"name"}, None, "new-name", id="set-name"
            ),
            pytest.param(
                None,
                "#00ff00",
                "new-name",
                {"color", "name"},
                "#00ff00",
                "new-name",
                id="set-both",
            ),
            pytest.param(
                None,
                "#00ff00",
                None,
                {"color"},
                "#00ff00",
                "test-repo",
                id="name-unchanged-when-omitted",
            ),
            pytest.param(
                "#ff0000",
                None,
                "new-name",
                {"name"},
                "#ff0000",
                "new-name",
                id="color-unchanged-when-omitted",
            ),
            pytest.param(
                "#ff0000", None, None, {"color"}, None, "test-repo", id="clear-color"
            ),
        ],
    )
    async def test_update_user_repo(
        self,
        persisted_user_repo_doc: UserRepoDocument,
        initial_color: str | None,
        color: str | None,
        name: str | None,
        fields_set: set[str],
        expected_color: str | None,
        expected_name: str,
    ) -> None:
        """Only fields present in fields_set are applied; omitted fields are unchanged."""
        if initial_color is not None:
            persisted_user_repo_doc.color = initial_color
            await persisted_user_repo_doc.save()

        result = await update_user_repo(
            persisted_user_repo_doc, color, name, fields_set
        )

        assert result.color == expected_color
        assert result.name == expected_name

        refreshed = await UserRepoDocument.get(persisted_user_repo_doc.id)
        assert refreshed is not None
        assert refreshed.color == expected_color
        assert refreshed.name == expected_name
