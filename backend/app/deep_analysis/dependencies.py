"""Deep analysis FastAPI dependencies.

This module defines reusable FastAPI dependencies for deep analysis-related
access verification and resource resolution.
"""

from typing import Annotated

from beanie import PydanticObjectId
from fastapi import Depends, HTTPException, status

from app.auth.dependencies import CurrentUser
from app.deep_analysis.models import DeepAnalysisDocument
from app.deep_analysis.schemas import CreateDeepAnalysisRequest
from app.deep_analysis.service import _mark_stale_analyses_as_failed
from app.users.dependencies import get_user_repo


async def verify_deep_analysis_repo_access(
    request: CreateDeepAnalysisRequest,
    user: CurrentUser,
) -> None:
    """Verify the authenticated user has access to the requested repository.

    Args:
        request(CreateDeepAnalysisRequest): The request containing `repo_id`.
        user(CurrentUser): The authenticated user (injected by FastAPI).
    """
    await get_user_repo(request.repo_id, user)


async def get_verified_deep_analysis(
    analysis_id: PydanticObjectId,
    user: CurrentUser,
) -> DeepAnalysisDocument:
    """Fetch a deep analysis and verify the user owns it.

    Lazy stale-detection: any RUNNING analysis whose last heartbeat is
    older than the stale threshold is flipped to FAILED before the read
    so the caller sees the resolved state. The flip is atomic via
    `update_many` filtered on this user's bucket — same Mongo round trip
    cost as before when no document is stale.

    Args:
        analysis_id(PydanticObjectId): The analysis document ID from the path parameter.
        user(CurrentUser): The authenticated user (injected by FastAPI).

    Returns:
        DeepAnalysisDocument: The verified deep analysis document.

    Raises:
        HTTPException: 404 if the analysis does not exist or does not belong to the user.
    """
    await _mark_stale_analyses_as_failed(user.uid)  # type: ignore[arg-type]

    analysis = await DeepAnalysisDocument.find_one(
        DeepAnalysisDocument.id == analysis_id,
        DeepAnalysisDocument.user_id == user.uid,
    )
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deep analysis not found.",
        )
    return analysis


DeepAnalysisRepoAccess = Annotated[None, Depends(verify_deep_analysis_repo_access)]
VerifiedDeepAnalysis = Annotated[
    DeepAnalysisDocument, Depends(get_verified_deep_analysis)
]
