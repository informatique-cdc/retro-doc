"""Pipeline service.

This module provides a client to start orchestrations on an Azure Durable
Functions app via its HTTP API.
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from beanie import PydanticObjectId
from beanie.operators import LT, In
from fastapi import HTTPException, status
from loguru import logger
from pymongo.results import UpdateResult

from app.pipeline.config import pipeline_settings
from app.pipeline.models import PipelineMeta, PipelineRunDocument, PipelineStatus

# Simple in-memory cache for the analyzer version with expiration
_version_cache: dict[str, Any] = {"version": None, "expires_at": 0}
_version_lock = asyncio.Lock()

_NOT_FOUND = "NotFound"

_DEAD_RUNTIME_STATUSES: dict[str, PipelineMeta] = {
    _NOT_FOUND: PipelineMeta(
        message="The analysis worker has no record of this run.", step="launch"
    ),
    "Failed": PipelineMeta(
        message="The analysis worker reported this run as failed.", step="run"
    ),
    "Terminated": PipelineMeta(
        message="The analysis was stopped before it finished.", step="run"
    ),
    "Canceled": PipelineMeta(
        message="The analysis was canceled before it finished.", step="run"
    ),
}


async def _probe_instance(instance_id: str) -> str | None:
    """Ask the worker what became of an orchestration instance.

    Args:
        instance_id(str): The orchestration's instance ID, which is the pipeline
            run's own ID.

    Returns:
        str | None: The instance's runtime status, `_NOT_FOUND` when the worker
            has no record of it, or None when it could not be asked — an outage
            must never read as an absent instance.
    """
    try:
        async with httpx.AsyncClient(
            base_url=pipeline_settings.DURABLE_FUNCTIONS_BASE_URL
        ) as client:
            # `showInput=false` keeps the caller's access token out of the response:
            # it defaults to true and a git orchestration's input carries that token.
            response = await client.get(
                f"/runtime/webhooks/durabletask/instances/{instance_id}",
                params={"showInput": "false"},
                timeout=3,
            )

        if response.status_code == status.HTTP_404_NOT_FOUND:
            return _NOT_FOUND

        if response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ):
            logger.warning(f"Pipeline: Not authorized to query instance {instance_id}.")
            return None

        response.raise_for_status()

        runtime_status: str = response.json()["runtimeStatus"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        logger.warning(f"Pipeline: Failed to query instance {instance_id}.")
        return None

    return runtime_status


async def get_analyzer_version() -> str:
    """Fetch the analysis worker's current analyzer version.

    Resolved fresh on every call, which is why write paths read it here: the
    version is pinned into the content-addressed repo identity, so a stale one
    would misroute deduplication.

    It also stores what it fetched, as the cache's only writer. Otherwise a
    repo stamped live just after a redeploy is judged by `is_stale` against a
    version the reader has not caught up to, answering wrongly in either
    direction until the TTL lapses. This closes that on the next write. A
    failed fetch raises before the store, so nothing unreported is cached.

    Returns:
        str: The worker's current analyzer version string.

    Raises:
        HTTPException: 502 if the worker is unreachable or returns an
            unexpected response.
    """
    try:
        async with httpx.AsyncClient(
            base_url=pipeline_settings.DURABLE_FUNCTIONS_BASE_URL
        ) as client:
            response = await client.get("/api/version", timeout=5)

        response.raise_for_status()
        version: str = response.json()["version"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.exception("Pipeline: Failed to fetch the analyzer version.")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the analyzer version.",
        ) from exc

    _version_cache["version"] = version
    _version_cache["expires_at"] = (
        time.time() + pipeline_settings.ANALYZER_VERSION_CACHE_TTL_S
    )

    return version


async def get_cached_analyzer_version() -> str:
    """Read the analysis worker's current analyzer version, cached with a TTL.

    For the read path only, where rendering freshness must not cost a worker
    call per repository. Never use it where the result is written: a stale read
    here mis-renders a flag, but there it would misroute deduplication.

    Reads only, delegating the fill so `get_analyzer_version` stays the sole
    writer: a store here would stamp the expiry from `now`, read before the
    lock and the fetch, cutting the TTL by however long those took.

    Returns:
        str: The worker's current analyzer version string.

    Raises:
        HTTPException: 502 if the worker is unreachable or returns an
            unexpected response.
    """
    now = time.time()

    cached: str | None = _version_cache["version"]
    if cached is not None and now < _version_cache["expires_at"]:
        return cached

    async with _version_lock:
        # Double-check after acquiring the lock
        cached = _version_cache["version"]
        if cached is not None and now < _version_cache["expires_at"]:
            return cached

        return await get_analyzer_version()


async def reconcile_pipeline_run(repo_id: PydanticObjectId) -> None:
    """Settle a repository's in-flight run when its instance is not live.

    A run left non-terminal holds the partial unique index forever, wedging the
    repository against every retry. Releasing that claim is a mutex release, so
    only FAILED is ever written, only past the grace period, and only while the
    status the probe answered for is still the one on disk.

    Best-effort: it settles only what the worker confirms dead, and otherwise
    leaves the run untouched, keeping the claim held rather than releasing it
    blind. Nothing sweeps in the background, so the next call is the retry.

    Args:
        repo_id(PydanticObjectId): The RepoDocument ID.
    """
    try:
        cutoff = datetime.now(UTC) - timedelta(
            seconds=pipeline_settings.PIPELINE_RUN_RECONCILE_GRACE_PERIOD_S
        )
        candidate = await PipelineRunDocument.find_one(
            PipelineRunDocument.repo_id == repo_id,
            In(
                PipelineRunDocument.status,
                [PipelineStatus.PENDING, PipelineStatus.RUNNING],
            ),
            LT(PipelineRunDocument.started_at, cutoff),
        )

        if candidate is None:
            return

        observed = candidate.status
        runtime_status = await _probe_instance(str(candidate.id))

        if runtime_status is None:
            return

        verdict = _DEAD_RUNTIME_STATUSES.get(runtime_status)

        if verdict is None:
            if runtime_status == "Completed":
                logger.warning(
                    f"Pipeline: Instance {candidate.id} of repo '{repo_id}' is "
                    f"completed while its run reads {observed.value}."
                )
            else:
                logger.debug(
                    f"Pipeline: Instance {candidate.id} of repo '{repo_id}' is "
                    f"{runtime_status}."
                )
            return

        # Atomic update: only settle the run if its status is still what we observed
        update_result: UpdateResult = await PipelineRunDocument.find_one(
            PipelineRunDocument.id == candidate.id,
            PipelineRunDocument.status == observed,
        ).set(
            {
                PipelineRunDocument.status: PipelineStatus.FAILED,
                PipelineRunDocument.finished_at: datetime.now(UTC),
                PipelineRunDocument.meta: verdict,
            }
        )

        if update_result.matched_count == 0:
            logger.debug(
                f"Pipeline: Run {candidate.id} of repo '{repo_id}' moved on before "
                "it could be settled."
            )
            return

        logger.info(
            f"Pipeline: Settled run {candidate.id} of repo '{repo_id}' as failed "
            f"({runtime_status})."
        )
    except Exception:
        logger.exception(f"Pipeline: Failed to reconcile a run of repo '{repo_id}'.")


async def start_orchestration(
    blob_path: str,
    languages: list[str],
    pipeline_run: PipelineRunDocument,
    *,
    repo_url: str | None = None,
    commit: str | None = None,
    ref: str | None = None,
    token: str | None = None,
) -> None:
    """Start the 'analyze' orchestration.

    Calls the Azure Durable Functions HTTP API to start a new orchestration
    instance. The request always carries a `source` tag (`"zip"` or `"git"`)
    telling the worker which payload shape it is looking at. For a zip,
    the worker reads source from `blob_path`. For a git repository (`repo_url`
    set), the worker clones the remote at `commit` and writes source under
    `blob_path`.

    The run's own ID goes out as the orchestration's `instance_id`, which Durable
    Functions accepts in place of its random default. That makes the run
    queryable: "no such instance" later proves this dispatch never landed. A
    duplicate needs no handling — a run dispatches once and a retry mints a new
    one, so an ID never goes out twice.

    Args:
        blob_path(str): The Azure Blob Storage path source is stored under.
        languages(list[str]): The languages to analyze (empty = all supported).
        pipeline_run(PipelineRunDocument): The MongoDB PipelineRunDocument to
            send to the orchestrator for tracking.
        repo_url(str | None): For a git source, the normalized repository URL.
        commit(str | None): For a git source, the commit SHA to check out.
        ref(str | None): For a git source, the branch the commit was resolved
            from (its tip equals commit), forwarded as a fetch hint so the
            worker can fetch that branch — enough to reach a non-default-branch
            tip. None for a bare mid-history commit, where the worker fetches
            more broadly.
        token(str | None): For a git source, the personal or project access
            token the worker authenticates its clone with. Never persisted or
            logged.

    Raises:
        HTTPException: 422 if the worker rejects the requested languages, 502 if
            the Durable Functions endpoint is unreachable or returns an
            unexpected response.
    """
    path = "/api/pipeline"
    payload: dict[str, Any] = {
        "source": "git" if repo_url is not None else "zip",
        "blob_path": blob_path,
        "languages": languages,
        "instance_id": str(pipeline_run.id),
        "repo_id": str(pipeline_run.repo_id),
    }
    if repo_url is not None:
        payload["repo_url"] = repo_url
        payload["commit"] = commit
        if ref is not None:
            payload["ref"] = ref
        if token is not None:
            payload["token"] = token

    try:
        async with httpx.AsyncClient(
            base_url=pipeline_settings.DURABLE_FUNCTIONS_BASE_URL
        ) as client:
            response = await client.post(path, json=payload, timeout=30)

        response.raise_for_status()
        data = response.json()
        instance_id: str = data["id"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.exception("Pipeline: Failed to start orchestration.")
        # A 422 means the worker rejected the languages
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 422:
            message = "One or more selected languages seems no longer supported."
            status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
        else:
            message = "Failed to start the analysis pipeline."
            status_code = status.HTTP_502_BAD_GATEWAY
        await pipeline_run.set(
            {
                PipelineRunDocument.status: PipelineStatus.FAILED,
                PipelineRunDocument.finished_at: datetime.now(UTC),
                PipelineRunDocument.meta: PipelineMeta(message=message, step="launch"),
            }
        )
        raise HTTPException(status_code=status_code, detail=message) from exc

    logger.debug(
        f"Pipeline: Started orchestration {instance_id} for repo '{pipeline_run.repo_id}'."
    )
