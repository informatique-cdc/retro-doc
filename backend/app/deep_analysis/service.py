"""Deep analysis service.

This module contains the business logic for the deep analysis feature.
The application is stateless: MongoDB is the single source of truth for
analysis status. Background tasks run via `asyncio.create_task` and are
prevented from garbage-collection by `_background_tasks`.
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import httpx
from beanie import PydanticObjectId
from fastapi import HTTPException, status
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger
from pymongo.results import UpdateResult

from app.auth.schemas import User
from app.chat.schemas import ChatContext
from app.deep_analysis.config import deep_analysis_settings
from app.deep_analysis.llm import get_deep_agent
from app.deep_analysis.models import DeepAnalysisDocument, DeepAnalysisStatus

_PDF_TEMPLATE_DIR = Path(__file__).resolve().parent / "pdf_template"

_background_tasks: set[asyncio.Task[None]] = set()


def _build_final_status_update(
    status: DeepAnalysisStatus, extra: dict[Any, Any] | None = None
) -> dict[str, dict[Any, Any]]:
    """Generate the update document for setting the final status of an analysis.

    Args:
        status(DeepAnalysisStatus): The final status to set (COMPLETED or FAILED).
        extra(dict[Any, Any] | None): Optional additional fields to set in the document.

    Returns:
        dict[str, dict[Any, Any]]: The MongoDB update document with `$set` and `$unset`.
    """
    now = datetime.now(UTC)
    set_payload: dict[Any, Any] = {
        DeepAnalysisDocument.status: status,
        DeepAnalysisDocument.finished_at: now,
    }
    if extra:
        set_payload.update(extra)
    return {
        "$set": set_payload,
        "$unset": {
            DeepAnalysisDocument.last_heartbeat_at: "",
        },
    }


@cache
def _load_pdf_html_template() -> bytes:
    """Get the HTML template used as the Gotenberg rendering shell.

    Returns:
        bytes: Raw content of ``index.html``.
    """
    return (_PDF_TEMPLATE_DIR / "index.html").read_bytes()


@cache
def _load_pdf_vendor_assets() -> tuple[tuple[str, bytes, str], ...]:
    """Get vendored CSS/JS assets bundled with each Gotenberg request.

    Gotenberg's container has no internet access, so CSS/JS that used to
    load from a CDN are bundled as extra multipart files. They are
    deliberately **not** inlined: Gotenberg parses `index.html` through
    Go's `html/template` engine, and minified JS routinely contains
    `{{`/`}}` sequences that the template engine misinterprets as
    actions, breaking the request before Chromium even starts.

    Loaded lazily so module import does not depend on the asset files
    being present.

    Returns:
        tuple[tuple[str, bytes, str], ...]: Tuples of (filename, content, MIME type).
    """
    return tuple(
        (name, (_PDF_TEMPLATE_DIR / name).read_bytes(), mime)
        for name, mime in (
            ("github-markdown-light.css", "text/css"),
            ("highlight.min.js", "application/javascript"),
            ("mermaid.min.js", "application/javascript"),
        )
    )


async def _mark_stale_analyses_as_failed(
    user_id: str, repo_id: PydanticObjectId | None = None
) -> None:
    """Atomically flip any of this user's RUNNING analyses whose heartbeat
    is older than the stale threshold to FAILED.

    Resilient to container restarts and stalled runs without a background
    sweeper: only callers that read the list are affected. One Mongo
    round trip; matches 0..n documents.

    Args:
        user_id(str): The user's unique identifier.
        repo_id(PydanticObjectId | None): Optional repository ID to filter by.
    """
    threshold = datetime.now(UTC) - timedelta(
        seconds=deep_analysis_settings.DEEP_AGENT_STALE_THRESHOLD_S
    )
    filters: list[Any] = [
        DeepAnalysisDocument.user_id == user_id,
        DeepAnalysisDocument.status == DeepAnalysisStatus.RUNNING,
        DeepAnalysisDocument.last_heartbeat_at < threshold,  # type: ignore[operator]
    ]
    if repo_id is not None:
        filters.append(DeepAnalysisDocument.repo_id == repo_id)

    await DeepAnalysisDocument.find(*filters).update_many(
        [  # type: ignore[arg-type]  # Aggregation pipeline update (list form)
            {
                "$set": {
                    "status": DeepAnalysisStatus.FAILED,
                    "finished_at": "$last_heartbeat_at",
                    "error": "Analysis interrupted (server restart or stalled). Please retry.",
                },
            },
            {
                "$unset": "last_heartbeat_at",
            },
        ]
    )


async def _run_deep_analysis(
    analysis_id: str,
    repo_id: str,
    query: str,
    user: User,
) -> None:
    """Run the deep analysis agent in the background.

    Streams progress to MongoDB via inline throttling so the frontend can
    observe the run. Updates the document as the analysis progresses:
    pending → running → completed/failed. Uses atomic find-and-set
    operations so concurrent deletions are handled without race conditions.

    Args:
        analysis_id(str): The analysis document ID.
        repo_id(str): The repository ID.
        query(str): The user's analysis query.
        user(User): The authenticated user.
    """
    oid = PydanticObjectId(analysis_id)
    now = datetime.now(UTC)
    flush_interval = deep_analysis_settings.DEEP_AGENT_PROGRESS_FLUSH_INTERVAL_S

    # Atomically set status to RUNNING; short-circuit if document was deleted
    update_result: UpdateResult = await DeepAnalysisDocument.find_one(
        DeepAnalysisDocument.id == oid,
    ).set(
        {
            DeepAnalysisDocument.status: DeepAnalysisStatus.RUNNING,
            DeepAnalysisDocument.last_heartbeat_at: now,
        },
    )

    if update_result.matched_count == 0:
        logger.warning(f"Deep Analysis: {analysis_id} - Document deleted before start.")
        return

    progress_current = 0
    result: dict[str, Any] | None = None
    last_flush_at = time.monotonic()

    try:
        agent = get_deep_agent()
        config: RunnableConfig = {
            "configurable": {"thread_id": analysis_id, "repo_id": repo_id},
        }

        stream = await agent.astream_events(
            {"messages": [{"role": "user", "content": query}]},
            config=config,
            context=ChatContext(username=user.name),
            version="v3",
        )

        async for event in stream:
            method = event.get("method")
            params = event.get("params") or {}
            is_subagent = bool(params.get("namespace"))
            data = params.get("data")

            if method == "messages":
                # data is (payload, metadata); payload is a dict with
                # `event` ∈ {message-start, content-block-*, message-finish}.
                # Count one AI model call per `message-start` (coordinator
                # OR subagent — both contribute to progress_total).
                payload = data[0] if isinstance(data, tuple | list) and data else None
                if (
                    isinstance(payload, dict)
                    and payload.get("event") == "message-start"
                    and payload.get("role") == "ai"
                ):
                    progress_current += 1
                    logger.debug(
                        f"Deep Analysis: {analysis_id} - Model call #{progress_current} (subagent={is_subagent})."
                    )
            elif method == "values" and not is_subagent:
                # Capture the latest coordinator state snapshot. The final
                # one before stream close is the agent's structured output.
                result = data

            # Inline throttle: at most one Mongo write per
            # DEEP_AGENT_PROGRESS_FLUSH_INTERVAL_S. Also serves as the
            # heartbeat for stale-detection (`last_heartbeat_at`).
            mono = time.monotonic()
            if mono - last_flush_at >= flush_interval:
                update_result = await DeepAnalysisDocument.find_one(
                    DeepAnalysisDocument.id == oid,
                ).set(
                    {
                        DeepAnalysisDocument.progress_current: progress_current,
                        DeepAnalysisDocument.last_heartbeat_at: datetime.now(UTC),
                    },
                )
                if update_result.matched_count == 0:
                    logger.warning(
                        f"Deep Analysis: {analysis_id} - Document deleted "
                        "during execution."
                    )
                    return
                last_flush_at = mono

        logger.debug(
            f"Deep Analysis: {analysis_id} - Stream finished after {progress_current} model calls."
        )

        if result is None:
            raise RuntimeError("Agent produced no usable output.")

        # Extract the structured report from ToolStrategy response, or
        # fall back to the last message when the agent exhausted its
        # tool budget before calling the response tool.
        if "structured_response" in result:
            content = result["structured_response"].report
        else:
            content = None
            for msg in reversed(result.get("messages", [])):
                if isinstance(msg, AIMessage) and msg.text:
                    content = msg.text
                    break
            if not content:
                raise RuntimeError("Agent produced no usable output.")

        # Atomically persist the completed result
        update_result = await DeepAnalysisDocument.find_one(
            DeepAnalysisDocument.id == oid,
        ).update(
            _build_final_status_update(
                DeepAnalysisStatus.COMPLETED,
                {
                    DeepAnalysisDocument.content: content,
                    DeepAnalysisDocument.progress_current: progress_current,
                },
            ),
        )

        if update_result.matched_count == 0:
            logger.warning(
                f"Deep Analysis: {analysis_id} - Document deleted during execution."
            )
            return

        logger.info(f"Deep Analysis: {analysis_id} - Completed.")

    except Exception:
        logger.exception("Deep Analysis {}: Failed.", analysis_id)

        # Atomically persist the failure
        update_result = await DeepAnalysisDocument.find_one(
            DeepAnalysisDocument.id == oid,
        ).update(
            _build_final_status_update(
                DeepAnalysisStatus.FAILED,
                {
                    DeepAnalysisDocument.error: (
                        "An error occurred during the analysis."
                    ),
                },
            ),
        )

        if update_result.matched_count == 0:
            logger.warning(
                f"Deep analysis: {analysis_id} - Document deleted during execution."
            )


async def delete_analyses_by_repo(user_id: str, repo_id: PydanticObjectId) -> None:
    """Delete all deep analyses for a user in a specific repository.

    Args:
        user_id(str): The user's unique identifier.
        repo_id(PydanticObjectId): The repository ID whose analyses should be removed.
    """
    await DeepAnalysisDocument.find(
        DeepAnalysisDocument.user_id == user_id,
        DeepAnalysisDocument.repo_id == repo_id,
    ).delete()


async def delete_analysis(analysis: DeepAnalysisDocument) -> None:
    """Delete a deep analysis document.

    If a background task is still running for this analysis, it will
    detect the deletion on its next save and exit gracefully.

    Args:
        analysis(DeepAnalysisDocument): The analysis document to delete.
    """
    await analysis.delete()


async def generate_analysis_pdf(analysis: DeepAnalysisDocument) -> bytes:
    """Generate a PDF from a completed deep analysis.

    Sends the markdown content to Gotenberg for conversion. The
    `waitForExpression` parameter is set to `window.__pdfReady === true`,
    a flag flipped by the in-page script once every Mermaid diagram has
    rendered, so the PDF capture happens after the SVGs are in the DOM.

    Args:
        analysis(DeepAnalysisDocument): The completed analysis document.

    Returns:
        bytes: The generated PDF file content.

    Raises:
        HTTPException: 502 if the PDF service is unreachable or returns an error.
    """
    base_url = deep_analysis_settings.GOTENBERG_BASE_URL
    endpoint = "/forms/chromium/convert/markdown"
    logger.debug(
        f"Deep Analysis: PDF request for {str(analysis.id)}",
    )
    try:
        async with httpx.AsyncClient(base_url=base_url) as client:
            response = await client.post(
                endpoint,
                files=[
                    ("files", ("index.html", _load_pdf_html_template(), "text/html")),
                    ("files", ("report.md", analysis.content or "", "text/markdown")),
                    *(
                        ("files", (name, data, mime))
                        for name, data, mime in _load_pdf_vendor_assets()
                    ),
                ],
                data={"waitForExpression": "window.__pdfReady === true"},
                timeout=60.0,
            )
            response.raise_for_status()
    except httpx.HTTPError:
        logger.exception(
            f"Deep Analysis: PDF service error for {str(analysis.id)}",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="PDF generation service is unavailable.",
        )
    return response.content


async def get_user_analyses(
    user: User,
    repo_id: PydanticObjectId | None = None,
) -> list[DeepAnalysisDocument]:
    """Get all deep analyses belonging to a user.

    Stale RUNNING analyses (no heartbeat past the threshold) are flipped
    to FAILED before the read so the caller sees the resolved state.

    Args:
        user(User): The authenticated user.
        repo_id(PydanticObjectId | None): Optional repository ID to filter by.

    Returns:
        list[DeepAnalysisDocument]: A list of analysis documents.
    """
    await _mark_stale_analyses_as_failed(user.uid, repo_id)  # type: ignore[arg-type]

    query = DeepAnalysisDocument.find(
        DeepAnalysisDocument.user_id == user.uid,
    )
    if repo_id is not None:
        query = query.find(DeepAnalysisDocument.repo_id == repo_id)
    return await query.sort("-created_at").to_list()


async def launch_deep_analysis(
    repo_id: PydanticObjectId, query: str, user: User
) -> DeepAnalysisDocument:
    """Create a deep analysis document and launch the background agent run.

    A strong reference to the task is kept in `_background_tasks` to
    prevent garbage-collection before completion. MongoDB remains the
    single source of truth for status.

    Args:
        repo_id(PydanticObjectId): The repository to analyze.
        query(str): The user's analysis query.
        user(User): The authenticated user.

    Returns:
        DeepAnalysisDocument: The newly created analysis document (status=pending).
    """
    analysis = DeepAnalysisDocument(
        user_id=user.uid,
        repo_id=repo_id,
        query=query,
    )
    await analysis.insert()

    task = asyncio.create_task(
        _run_deep_analysis(
            str(analysis.id),
            str(repo_id),
            query,
            user,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return analysis
