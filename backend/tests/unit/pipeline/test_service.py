"""Unit tests for the pipeline service.

This module tests orchestration dispatch, analyzer version fetching and its
TTL cache, the Durable Functions instance probe, and run reconciliation.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from app.pipeline import service
from app.pipeline.config import pipeline_settings
from app.pipeline.models import PipelineRunDocument, PipelineStatus
from app.pipeline.service import (
    get_analyzer_version,
    get_cached_analyzer_version,
    reconcile_pipeline_run,
    start_orchestration,
)
from tests.unit.mocks.httpx_client import (
    mock_failing_httpx_client,
    mock_httpx_client,
    mock_status_httpx_client,
)

_GIT_URL = "https://github.com/octo/repo"
_GIT_COMMIT = "a" * 40
_GIT_KWARGS = {"repo_url": _GIT_URL, "commit": _GIT_COMMIT}
_GIT_FIELDS = {"source": "git", "repo_url": _GIT_URL, "commit": _GIT_COMMIT}


async def _insert_run(
    repo_id: PydanticObjectId,
    *,
    run_status: PipelineStatus = PipelineStatus.PENDING,
    age_s: int = pipeline_settings.PIPELINE_RUN_RECONCILE_GRACE_PERIOD_S + 60,
) -> PipelineRunDocument:
    """Persist a run of a chosen status and age, past the grace period by default."""
    run = PipelineRunDocument(
        repo_id=repo_id,
        status=run_status,
        started_at=datetime.now(UTC) - timedelta(seconds=age_s),
    )
    await run.insert()
    return run


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty the module-level analyzer version cache for each test."""
    monkeypatch.setattr(service, "_version_cache", {"version": None, "expires_at": 0})


class TestGetAnalyzerVersion:
    """Read the analyzer version live from the worker on every call."""

    async def test_version_fetches(
        self, mock_httpx: Callable[[AsyncMock], AsyncMock]
    ) -> None:
        """Parses and returns the worker's `version` field."""
        mock_httpx(mock_httpx_client({"version": "abc123"}))

        assert await get_analyzer_version() == "abc123"

    async def test_version_fetches_fresh_each_call(
        self, mock_httpx: Callable[[AsyncMock], AsyncMock]
    ) -> None:
        """The worker is consulted on every call, never served from the cache."""
        client = mock_httpx(mock_httpx_client({"version": "abc123"}))

        await get_analyzer_version()
        await get_analyzer_version()

        assert client.get.await_count == 2

    async def test_version_publishes_what_it_fetched(
        self, mock_httpx: Callable[[AsyncMock], AsyncMock]
    ) -> None:
        """The live fetch fills the cache, making this its only writer."""
        mock_httpx(mock_httpx_client({"version": "abc123"}))

        await get_analyzer_version()

        assert service._version_cache["version"] == "abc123"
        assert service._version_cache["expires_at"] > time.time()

    async def test_version_primes_the_read_path(
        self, mock_httpx: Callable[[AsyncMock], AsyncMock]
    ) -> None:
        """A write path's live read drags the `stale` flag onto the same version."""
        client = mock_httpx(mock_httpx_client({"version": "abc123"}))

        await get_analyzer_version()
        cached = await get_cached_analyzer_version()

        assert cached == "abc123"
        assert client.get.await_count == 1

    async def test_version_failed_fetch_leaves_the_cache_intact(
        self,
        mock_httpx: Callable[[AsyncMock], AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A 502 raises before the store, so it cannot poison what readers see."""
        monkeypatch.setattr(
            service,
            "_version_cache",
            {"version": "abc123", "expires_at": time.time() + 300},
        )
        mock_httpx(
            mock_failing_httpx_client(request_error=httpx.ConnectError("refused"))
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_analyzer_version()

        assert exc_info.value.status_code == 502
        assert service._version_cache["version"] == "abc123"

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(
                {"request_error": httpx.ConnectError("Connection refused")},
                id="connect-error",
            ),
            pytest.param(
                {"status_error": httpx.HTTPError("500 Server Error")},
                id="bad-status",
            ),
            pytest.param({"json_value": {}}, id="missing-version-key"),
            pytest.param({"json_value": None}, id="malformed-body"),
        ],
    )
    async def test_version_worker_error_raises_502(
        self,
        failure: dict[str, Any],
        mock_httpx: Callable[[AsyncMock], AsyncMock],
    ) -> None:
        """Any worker/parsing failure surfaces as HTTP 502."""
        mock_httpx(mock_failing_httpx_client(**failure))

        with pytest.raises(HTTPException) as exc_info:
            await get_analyzer_version()

        assert exc_info.value.status_code == 502


class TestGetCachedAnalyzerVersion:
    """Serve the analyzer version from a TTL cache, delegating every miss."""

    async def test_cached_version_serves_from_cache(
        self, mock_httpx: Callable[[AsyncMock], AsyncMock]
    ) -> None:
        """A repeated read hits the worker once, so listing N repos costs one call."""
        client = mock_httpx(mock_httpx_client({"version": "abc123"}))

        first = await get_cached_analyzer_version()
        second = await get_cached_analyzer_version()

        assert first == second == "abc123"
        assert client.get.await_count == 1

    async def test_cached_version_refreshes_when_expired(
        self,
        mock_httpx: Callable[[AsyncMock], AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Once the TTL lapses the worker is consulted again."""
        client = mock_httpx(mock_httpx_client({"version": "abc123"}))

        await get_cached_analyzer_version()
        monkeypatch.setitem(service._version_cache, "expires_at", 0)
        await get_cached_analyzer_version()

        assert client.get.await_count == 2

    async def test_cached_version_delegates_the_fill(
        self,
        mock_httpx: Callable[[AsyncMock], AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A miss is filled by `get_analyzer_version`, which owns the store."""
        client = mock_httpx(mock_httpx_client({"version": "abc123"}))
        live = AsyncMock(return_value="abc123")
        monkeypatch.setattr(service, "get_analyzer_version", live)

        version = await get_cached_analyzer_version()

        assert version == "abc123"
        live.assert_awaited_once()
        assert client.get.await_count == 0

    async def test_cached_version_worker_error_raises_502(
        self, mock_httpx: Callable[[AsyncMock], AsyncMock]
    ) -> None:
        """A worker failure surfaces as 502 and leaves nothing cached."""
        mock_httpx(
            mock_failing_httpx_client(request_error=httpx.ConnectError("refused"))
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_cached_analyzer_version()

        assert exc_info.value.status_code == 502
        assert service._version_cache["version"] is None


class TestProbeInstance:
    """Report a Durable Functions instance's runtime status, or nothing."""

    async def test_probe_reads_the_runtime_status(
        self, mock_httpx: Callable[[AsyncMock], AsyncMock]
    ) -> None:
        """The worker's `runtimeStatus` comes back verbatim."""
        mock_httpx(mock_httpx_client({"runtimeStatus": "Running"}))

        assert await service._probe_instance("abc") == "Running"

    async def test_probe_hides_the_orchestration_input(
        self, mock_httpx: Callable[[AsyncMock], AsyncMock]
    ) -> None:
        """`showInput=false` keeps the caller's access token out of the response."""
        client = mock_httpx(mock_httpx_client({"runtimeStatus": "Running"}))

        await service._probe_instance("abc")

        assert client.get.call_args.kwargs["params"] == {"showInput": "false"}

    async def test_probe_reports_an_absent_instance(
        self, mock_httpx: Callable[[AsyncMock], AsyncMock]
    ) -> None:
        """A 404 is the one answer proving the instance is not live."""
        mock_httpx(mock_status_httpx_client(404))

        assert await service._probe_instance("abc") == service._NOT_FOUND

    @pytest.mark.parametrize(
        "status_code",
        [
            pytest.param(401, id="unauthorized"),
            pytest.param(403, id="forbidden"),
            pytest.param(500, id="server-error"),
            pytest.param(503, id="service-unavailable"),
        ],
    )
    async def test_probe_unreadable_status_is_never_absence(
        self,
        status_code: int,
        mock_httpx: Callable[[AsyncMock], AsyncMock],
    ) -> None:
        """A rejection or an outage answers None, never `_NOT_FOUND`."""
        mock_httpx(mock_status_httpx_client(status_code))

        assert await service._probe_instance("abc") is None

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(
                {"request_error": httpx.ConnectTimeout("timed out")}, id="timeout"
            ),
            pytest.param({"json_value": {}}, id="missing-runtime-status"),
            pytest.param({"json_value": None}, id="malformed-body"),
        ],
    )
    async def test_probe_unreadable_response_is_never_absence(
        self,
        failure: dict[str, Any],
        mock_httpx: Callable[[AsyncMock], AsyncMock],
    ) -> None:
        """A body that cannot be parsed answers None, never `_NOT_FOUND`."""
        mock_httpx(mock_failing_httpx_client(**failure))

        assert await service._probe_instance("abc") is None


class TestReconcilePipelineRun:
    """Fail a repository's wedged run once the worker proves it is not live."""

    @pytest.fixture
    def mock_probe_instance(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        """Patch the Durable Functions instance probe."""
        mock = AsyncMock()
        monkeypatch.setattr(service, "_probe_instance", mock)
        return mock

    @pytest.mark.parametrize(
        "run_status",
        [
            pytest.param(PipelineStatus.COMPLETED, id="completed"),
            pytest.param(PipelineStatus.FAILED, id="failed"),
        ],
    )
    async def test_reconcile_never_probes_a_settled_run(
        self,
        run_status: PipelineStatus,
        mock_probe_instance: AsyncMock,
        repo_id: PydanticObjectId,
    ) -> None:
        """Only PENDING/RUNNING is probed: the worker purges terminal history."""
        await _insert_run(repo_id, run_status=run_status)

        await reconcile_pipeline_run(repo_id)

        mock_probe_instance.assert_not_awaited()

    async def test_reconcile_never_probes_inside_the_grace_period(
        self, mock_probe_instance: AsyncMock, repo_id: PydanticObjectId
    ) -> None:
        """A fresh claim is left to the instance-creation window it is still inside."""
        await _insert_run(repo_id, age_s=0)

        await reconcile_pipeline_run(repo_id)

        mock_probe_instance.assert_not_awaited()

    @pytest.mark.parametrize(
        ("run_status", "runtime_status", "expected_step"),
        [
            pytest.param(
                PipelineStatus.PENDING, "NotFound", "launch", id="never-landed"
            ),
            pytest.param(PipelineStatus.PENDING, "Failed", "run", id="failed"),
            pytest.param(PipelineStatus.PENDING, "Terminated", "run", id="terminated"),
            pytest.param(PipelineStatus.PENDING, "Canceled", "run", id="canceled"),
            pytest.param(
                PipelineStatus.RUNNING, "NotFound", "launch", id="running-abandoned"
            ),
        ],
    )
    async def test_reconcile_settles_a_dead_instance(
        self,
        run_status: PipelineStatus,
        runtime_status: str,
        expected_step: str,
        mock_probe_instance: AsyncMock,
        repo_id: PydanticObjectId,
    ) -> None:
        """A run whose instance is not live is failed, releasing the claim.

        RUNNING wedges just as hard as PENDING: nothing in the backend moves it on.
        So it is settled the same way.
        """
        mock_probe_instance.return_value = runtime_status
        run = await _insert_run(repo_id, run_status=run_status)

        await reconcile_pipeline_run(repo_id)

        refreshed = await PipelineRunDocument.get(run.id)
        assert refreshed is not None
        assert refreshed.status == PipelineStatus.FAILED
        assert refreshed.finished_at is not None
        assert refreshed.meta is not None
        assert refreshed.meta.step == expected_step

    @pytest.mark.parametrize(
        "runtime_status",
        [
            pytest.param("Pending", id="queued"),
            pytest.param("Running", id="live"),
            pytest.param("Completed", id="succeeded"),
            pytest.param(None, id="unanswerable"),
        ],
    )
    async def test_reconcile_writes_nothing_else(
        self,
        runtime_status: str | None,
        mock_probe_instance: AsyncMock,
        repo_id: PydanticObjectId,
    ) -> None:
        """Anything short of proof of death is left alone.

        `Completed` included: the worker owns progress and success, so a run it has
        not stamped yet is its business, not this function's.
        """
        mock_probe_instance.return_value = runtime_status
        run = await _insert_run(repo_id)

        await reconcile_pipeline_run(repo_id)

        refreshed = await PipelineRunDocument.get(run.id)
        assert refreshed is not None
        assert refreshed.status == PipelineStatus.PENDING

    async def test_reconcile_does_not_kill_a_run_the_worker_just_picked_up(
        self, mock_probe_instance: AsyncMock, repo_id: PydanticObjectId
    ) -> None:
        """The write is guarded on the status the probe answered for.

        The probe is an await and the worker writes out of process, so PENDING can
        become RUNNING in between. A guard of `In([PENDING, RUNNING])` would read
        that as the same run and fail one that had just started.
        """
        run = await _insert_run(repo_id)

        async def _pick_up(instance_id: str) -> str:
            await PipelineRunDocument.find_one(PipelineRunDocument.id == run.id).set(
                {PipelineRunDocument.status: PipelineStatus.RUNNING}
            )
            return service._NOT_FOUND

        mock_probe_instance.side_effect = _pick_up

        await reconcile_pipeline_run(repo_id)

        refreshed = await PipelineRunDocument.get(run.id)
        assert refreshed is not None
        assert refreshed.status == PipelineStatus.RUNNING

    async def test_reconcile_swallows_its_own_failure(
        self, mock_probe_instance: AsyncMock, repo_id: PydanticObjectId
    ) -> None:
        """It repairs damage its caller did not cause, so it never breaks a request."""
        mock_probe_instance.side_effect = RuntimeError("boom")
        run = await _insert_run(repo_id)

        await reconcile_pipeline_run(repo_id)

        refreshed = await PipelineRunDocument.get(run.id)
        assert refreshed is not None
        assert refreshed.status == PipelineStatus.PENDING


class TestStartOrchestration:
    """Dispatch an analysis to the worker, failing the run when it cannot."""

    @pytest.fixture
    def mock_pipeline_run_doc(self) -> MagicMock:
        """A mock `PipelineRunDocument` with preset `id` and `repo_id`."""
        run = MagicMock(spec=PipelineRunDocument)
        run.id = PydanticObjectId("aaaaaaaaaaaaaaaaaaaaaaaa")
        run.repo_id = PydanticObjectId("bbbbbbbbbbbbbbbbbbbbbbbb")
        run.set = AsyncMock()
        return run

    @pytest.fixture
    async def persisted_pipeline_run_doc(
        self, repo_id: PydanticObjectId
    ) -> PipelineRunDocument:
        """A persisted `PipelineRunDocument`."""
        doc = PipelineRunDocument(repo_id=repo_id)
        await doc.insert()
        return doc

    async def test_connect_error_marks_run_as_failed(
        self,
        blob_path: str,
        mock_httpx: Callable[[AsyncMock], AsyncMock],
        persisted_pipeline_run_doc: PipelineRunDocument,
    ) -> None:
        """A connection error marks the run `FAILED` in the database."""
        mock_httpx(
            mock_failing_httpx_client(
                method="post", request_error=httpx.ConnectError("Connection refused")
            )
        )

        with pytest.raises(HTTPException) as exc_info:
            await start_orchestration(blob_path, ["java"], persisted_pipeline_run_doc)

        assert exc_info.value.status_code == 502

        refreshed = await PipelineRunDocument.get(persisted_pipeline_run_doc.id)
        assert refreshed is not None
        assert refreshed.status == PipelineStatus.FAILED
        assert refreshed.finished_at is not None

    async def test_missing_id_key_raises_502(
        self,
        blob_path: str,
        mock_httpx: Callable[[AsyncMock], AsyncMock],
        mock_pipeline_run_doc: MagicMock,
    ) -> None:
        """Response JSON missing the `id` key is a 502 that fails the run."""
        mock_httpx(mock_httpx_client({"no_id": "value"}, method="post"))

        with pytest.raises(HTTPException) as exc_info:
            await start_orchestration(blob_path, ["java"], mock_pipeline_run_doc)

        assert exc_info.value.status_code == 502
        assert exc_info.value.detail == "Failed to start the analysis pipeline."
        mock_pipeline_run_doc.set.assert_awaited_once()
        update_dict = mock_pipeline_run_doc.set.call_args.args[0]
        assert PipelineStatus.FAILED in update_dict.values()

    async def test_malformed_body_raises_502(
        self,
        blob_path: str,
        mock_httpx: Callable[[AsyncMock], AsyncMock],
        mock_pipeline_run_doc: MagicMock,
    ) -> None:
        """A literal `null` body is a 502 like any other unreadable response."""
        mock_httpx(mock_failing_httpx_client(json_value=None, method="post"))

        with pytest.raises(HTTPException) as exc_info:
            await start_orchestration(blob_path, ["java"], mock_pipeline_run_doc)

        assert exc_info.value.status_code == 502
        mock_pipeline_run_doc.set.assert_awaited_once()

    async def test_success_preserves_pending_status(
        self,
        blob_path: str,
        mock_httpx: Callable[[AsyncMock], AsyncMock],
        persisted_pipeline_run_doc: PipelineRunDocument,
    ) -> None:
        """On success the run stays `PENDING` in the database."""
        mock_httpx(mock_httpx_client({"id": "instance-123"}, method="post"))

        await start_orchestration(blob_path, ["java"], persisted_pipeline_run_doc)

        refreshed = await PipelineRunDocument.get(persisted_pipeline_run_doc.id)
        assert refreshed is not None
        assert refreshed.status == PipelineStatus.PENDING

    async def test_unprocessable_notifies_unsupported_languages(
        self,
        blob_path: str,
        mock_httpx: Callable[[AsyncMock], AsyncMock],
        persisted_pipeline_run_doc: PipelineRunDocument,
    ) -> None:
        """A worker 422 yields HTTP 422 with a clear message and FAILED meta."""
        mock_httpx(mock_status_httpx_client(422, method="post"))

        with pytest.raises(HTTPException) as exc_info:
            await start_orchestration(blob_path, ["cobol"], persisted_pipeline_run_doc)

        assert exc_info.value.status_code == 422
        assert "languages" in exc_info.value.detail.lower()

        refreshed = await PipelineRunDocument.get(persisted_pipeline_run_doc.id)
        assert refreshed is not None
        assert refreshed.status == PipelineStatus.FAILED
        assert refreshed.finished_at is not None
        assert refreshed.meta is not None
        assert "languages" in refreshed.meta.message.lower()

    @pytest.mark.parametrize(
        ("languages", "source_kwargs", "expected_source_fields"),
        [
            pytest.param(["java"], {}, {"source": "zip"}, id="zip"),
            pytest.param([], _GIT_KWARGS, _GIT_FIELDS, id="git-public"),
            pytest.param(
                [],
                {**_GIT_KWARGS, "ref": "main"},
                {**_GIT_FIELDS, "ref": "main"},
                id="git-with-ref",
            ),
            pytest.param(
                [],
                {**_GIT_KWARGS, "token": "glpat-secret"},
                {**_GIT_FIELDS, "token": "glpat-secret"},
                id="git-private",
            ),
        ],
    )
    async def test_orchestration_payload(
        self,
        languages: list[str],
        source_kwargs: dict[str, Any],
        expected_source_fields: dict[str, Any],
        blob_path: str,
        mock_httpx: Callable[[AsyncMock], AsyncMock],
        persisted_pipeline_run_doc: PipelineRunDocument,
    ) -> None:
        """The payload tags its `source`, then carries exactly what that source requires."""
        client = mock_httpx(mock_httpx_client({"id": "instance-123"}, method="post"))

        await start_orchestration(
            blob_path, languages, persisted_pipeline_run_doc, **source_kwargs
        )

        payload = client.post.call_args.kwargs["json"]
        assert payload == {
            "blob_path": blob_path,
            "languages": languages,
            "instance_id": str(persisted_pipeline_run_doc.id),
            "repo_id": str(persisted_pipeline_run_doc.repo_id),
            **expected_source_fields,
        }

    async def test_conflict_marks_run_as_failed(
        self,
        blob_path: str,
        mock_httpx: Callable[[AsyncMock], AsyncMock],
        persisted_pipeline_run_doc: PipelineRunDocument,
    ) -> None:
        """A 409 fails the run rather than crediting a dispatch it cannot confirm.

        Durable Functions reports a duplicate `instance_id` as an exception inside
        the worker, never as an HTTP status of its own, so no code is evidence the
        orchestration is up. Reading one as success would leave the run PENDING,
        holding the claim its repository needs to retry.
        """
        mock_httpx(mock_status_httpx_client(409, method="post"))

        with pytest.raises(HTTPException) as exc_info:
            await start_orchestration(blob_path, ["java"], persisted_pipeline_run_doc)

        assert exc_info.value.status_code == 502

        refreshed = await PipelineRunDocument.get(persisted_pipeline_run_doc.id)
        assert refreshed is not None
        assert refreshed.status == PipelineStatus.FAILED
