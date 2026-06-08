"""Deep analysis Pydantic schemas.

This module defines the schemas for the deep analysis endpoints (DTOs).
"""

from datetime import datetime

from beanie import PydanticObjectId
from pydantic import BaseModel

from app.deep_analysis.models import DeepAnalysisStatus


class CreateDeepAnalysisRequest(BaseModel):
    repo_id: PydanticObjectId
    query: str


class DeepAnalysisResponse(BaseModel):
    id: PydanticObjectId
    repo_id: PydanticObjectId
    query: str
    status: DeepAnalysisStatus
    created_at: datetime
    finished_at: datetime | None = None


class DeepAnalysisListResponse(BaseModel):
    analyses: list[DeepAnalysisResponse]


class DeepAnalysisDetailResponse(DeepAnalysisResponse):
    content: str | None = None
    error: str | None = None
    progress_current: int = 0
    progress_total: int = 0
