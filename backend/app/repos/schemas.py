"""Repos Pydantic schemas.

This module defines the schemas for the repository endpoints (e.g., Data Transfer Object - DTO).
"""

from datetime import datetime
from typing import Any

from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from app.docs.models import AnalysisStats
from app.pipeline.models import PipelineMeta, PipelineStatus


class CreateRepoResponse(BaseModel):
    repo_id: PydanticObjectId
    status: PipelineStatus = PipelineStatus.PENDING


class JoinRepoResponse(BaseModel):
    repo_id: PydanticObjectId
    name: str


class PipelineAttempt(BaseModel):
    """One analysis attempt on a repository.

    `retried_at` is set on the attempts a retry superseded, so a client can tell
    a failure that was restarted from one that still stands.
    """

    status: PipelineStatus
    started_at: datetime
    finished_at: datetime | None = None
    retried_at: datetime | None = None
    meta: PipelineMeta | None = None


class PipelineStatusResponse(BaseModel):
    """A repository's pipeline state, current attempt plus the ones before it.

    `status` and `meta` describe the latest attempt — `attempts[0]` — and keep
    the meaning they had before the list existed. `attempts` is never empty: a
    repository with no runs is a 404.
    """

    repo_id: PydanticObjectId
    status: PipelineStatus
    meta: PipelineMeta | None = None
    attempts: list[PipelineAttempt] = Field(default_factory=list)


class RelaunchRepoRequest(BaseModel):
    token: str | None = None


class RelaunchRepoResponse(BaseModel):
    repo_id: PydanticObjectId
    status: PipelineStatus = PipelineStatus.PENDING


class RepoResponse(BaseModel):
    repo_id: PydanticObjectId
    name: str
    repo_url: str
    repo_hash: str | None
    languages: list[str]
    analyzer_version: str | None
    stale: bool
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
