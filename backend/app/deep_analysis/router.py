"""Deep analysis router.

This module defines the API endpoints related to deep analysis.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from app.auth.dependencies import CurrentUser
from app.deep_analysis.config import deep_analysis_settings
from app.deep_analysis.dependencies import (
    DeepAnalysisRepoAccess,
    VerifiedDeepAnalysis,
)
from app.deep_analysis.llm import (
    close_deep_agent_resources,
    init_deep_agent_resources,
)
from app.deep_analysis.models import DeepAnalysisStatus
from app.deep_analysis.schemas import (
    CreateDeepAnalysisRequest,
    DeepAnalysisDetailResponse,
    DeepAnalysisListResponse,
    DeepAnalysisResponse,
)
from app.deep_analysis.service import (
    delete_analysis,
    generate_analysis_pdf,
    get_user_analyses,
    launch_deep_analysis,
)


@asynccontextmanager
async def deep_analysis_lifespan(_: APIRouter) -> AsyncGenerator[Any, Any]:
    init_deep_agent_resources()
    yield
    close_deep_agent_resources()


deep_analysis_router = APIRouter(
    prefix="/deep-analysis", tags=["deep-analysis"], lifespan=deep_analysis_lifespan
)


@deep_analysis_router.post(
    "",
    response_model=DeepAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_deep_analysis_endpoint(
    request: CreateDeepAnalysisRequest,
    user: CurrentUser,
    _access: DeepAnalysisRepoAccess,
) -> DeepAnalysisResponse:
    """Launch a new deep analysis on a repository.

    Creates a deep analysis document and starts the agent in the background.
    Returns immediately with the analysis ID for polling.

    Args:
        request(CreateDeepAnalysisRequest): The request containing `repo_id`
            and the user's analysis query.
        user(CurrentUser): The authenticated user (injected by FastAPI).
        _access(DeepAnalysisRepoAccess): Dependency to verify the user has
            access to the repo (injected by FastAPI).

    Returns:
        DeepAnalysisResponse: The newly created analysis with status 'pending'.
    """
    analysis = await launch_deep_analysis(request.repo_id, request.query, user)

    return DeepAnalysisResponse(
        id=analysis.id,  # type: ignore[arg-type]
        repo_id=analysis.repo_id,
        query=analysis.query,
        status=analysis.status,
        created_at=analysis.created_at,
        finished_at=analysis.finished_at,
    )


@deep_analysis_router.get("", response_model=DeepAnalysisListResponse)
async def list_deep_analyses_endpoint(
    user: CurrentUser,
    repo_id: PydanticObjectId | None = None,
) -> DeepAnalysisListResponse:
    """List all deep analyses for the authenticated user.

    Analyses are returned in descending order of `created_at`.
    Optionally filtered by `repo_id`.

    Args:
        user(CurrentUser): The authenticated user (injected by FastAPI).
        repo_id(PydanticObjectId | None): Optional repository ID to filter by.

    Returns:
        DeepAnalysisListResponse: A list of the user's deep analyses.
    """
    analyses = await get_user_analyses(user, repo_id)
    max_len = deep_analysis_settings.QUERY_PREVIEW_MAX_LENGTH

    return DeepAnalysisListResponse(
        analyses=[
            DeepAnalysisResponse(
                id=a.id,  # type: ignore[arg-type]
                repo_id=a.repo_id,
                query=a.query[:max_len] + "..." if len(a.query) > max_len else a.query,
                status=a.status,
                created_at=a.created_at,
                finished_at=a.finished_at,
            )
            for a in analyses
        ]
    )


@deep_analysis_router.delete("/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deep_analysis_endpoint(
    analysis: VerifiedDeepAnalysis,
) -> None:
    """Delete a deep analysis.

    If a background task is still running, it will detect the deletion
    and exit gracefully.

    Args:
        analysis(VerifiedDeepAnalysis): The verified analysis document
            (injected by FastAPI).
    """
    await delete_analysis(analysis)


@deep_analysis_router.get(
    "/{analysis_id}",
    response_model=DeepAnalysisDetailResponse,
    response_model_exclude_none=True,
)
async def get_deep_analysis_endpoint(
    analysis: VerifiedDeepAnalysis,
) -> DeepAnalysisDetailResponse:
    """Get the status and details of a deep analysis.

    Returns the full analysis detail including content (if completed)
    and error (if failed).

    Args:
        analysis(VerifiedDeepAnalysis): The verified analysis document
            (injected by FastAPI).

    Returns:
        DeepAnalysisDetailResponse: The analysis details.
    """
    return DeepAnalysisDetailResponse(
        id=analysis.id,  # type: ignore[arg-type]
        repo_id=analysis.repo_id,
        query=analysis.query,
        status=analysis.status,
        progress_current=analysis.progress_current,
        progress_total=analysis.progress_total,
        content=analysis.content,
        error=analysis.error,
        created_at=analysis.created_at,
        finished_at=analysis.finished_at,
    )


@deep_analysis_router.get("/{analysis_id}/pdf")
async def download_deep_analysis_pdf_endpoint(
    analysis: VerifiedDeepAnalysis,
) -> StreamingResponse:
    """Download the completed deep analysis as a PDF.

    Only available when the analysis status is `completed`.

    Args:
        analysis(VerifiedDeepAnalysis): The verified analysis document
            (injected by FastAPI).

    Returns:
        StreamingResponse: The PDF file as a downloadable response.

    Raises:
        HTTPException: 409 if the analysis is not yet completed.
    """
    if analysis.status != DeepAnalysisStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Analysis is not yet completed.",
        )

    pdf_bytes = await generate_analysis_pdf(analysis)

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="deep-analysis-{analysis.id}.pdf"'
            )
        },
    )
