"""Repos Pydantic schemas.

This module defines the schemas for the repository endpoints (e.g., Data Transfer Object - DTO).
"""

from datetime import datetime
from typing import Any

from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from app.docs.models import AnalysisStats
from app.pipeline.models import PipelineMeta, PipelineStatus


class AnalyzeFileResponse(BaseModel):
    repo_id: PydanticObjectId
    status: PipelineStatus = PipelineStatus.PENDING


class JoinRepoResponse(BaseModel):
    repo_id: PydanticObjectId
    name: str


class PipelineStatusResponse(BaseModel):
    repo_id: PydanticObjectId
    status: PipelineStatus
    meta: PipelineMeta | None = None


class RepoResponse(BaseModel):
    repo_id: PydanticObjectId
    name: str
    repo_url: str | None
    repo_branch: str | None
    repo_hash: str | None
    languages: list[str]
    color: str | None = None
    created_at: datetime
    updated_at: datetime


class RepoDetailResponse(RepoResponse):
    content: str | None = None
    stats: AnalysisStats | None = None


class RepoListResponse(BaseModel):
    repos: list[RepoResponse]


class UpdateUserRepoRequest(BaseModel):
    name: str | None = None
    color: str | None = Field(default=None, max_length=50)


class FileResponse(BaseModel):
    file_id: PydanticObjectId
    path: str
    file_hash: str


class RepoFilesResponse(BaseModel):
    repo_id: PydanticObjectId
    files: list[FileResponse]


class FileDocumentationResponse(BaseModel):
    repo_id: PydanticObjectId
    file_id: PydanticObjectId
    content: str


class ScopedGraph(BaseModel):
    scope: str | None
    content: dict[str, Any]


class FileSourceResponse(BaseModel):
    repo_id: PydanticObjectId
    file_id: PydanticObjectId
    path: str
    content: str


class FileGraphsResponse(BaseModel):
    repo_id: PydanticObjectId
    file_id: PydanticObjectId
    ast: dict[str, Any] | None
    cfg: list[ScopedGraph]
    dfg: list[ScopedGraph]
