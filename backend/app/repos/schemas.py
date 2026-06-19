"""Repos Pydantic schemas.

This module defines the schemas for the repository endpoints (e.g., Data Transfer Object - DTO).
"""

from datetime import datetime
from typing import Any

from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from app.core.language_enum import Language
from app.pipeline.models import PipelineMeta, PipelineStatus


class AnalyzeFileResponseModel(BaseModel):
    repo_id: PydanticObjectId
    status: PipelineStatus = PipelineStatus.PENDING


class JoinRepoResponseModel(BaseModel):
    repo_id: PydanticObjectId
    name: str


class PipelineStatusResponseModel(BaseModel):
    repo_id: PydanticObjectId
    status: PipelineStatus
    meta: PipelineMeta | None = None


class RepoResponseModel(BaseModel):
    repo_id: PydanticObjectId
    name: str
    repo_url: str | None
    repo_branch: str | None
    repo_hash: str | None
    language: Language
    color: str | None = None
    created_at: datetime
    updated_at: datetime


class RepoDetailResponseModel(RepoResponseModel):
    content: str | None = None


class RepoListResponseModel(BaseModel):
    repos: list[RepoResponseModel]


class UpdateUserRepoRequest(BaseModel):
    name: str | None = None
    color: str | None = Field(default=None, max_length=50)


class FileResponseModel(BaseModel):
    file_id: PydanticObjectId
    path: str
    file_hash: str


class RepoFilesResponseModel(BaseModel):
    repo_id: PydanticObjectId
    files: list[FileResponseModel]


class FileDocumentationResponseModel(BaseModel):
    repo_id: PydanticObjectId
    file_id: PydanticObjectId
    content: str


class ScopedGraphModel(BaseModel):
    scope: str | None
    content: dict[str, Any]


class FileSourceResponseModel(BaseModel):
    repo_id: PydanticObjectId
    file_id: PydanticObjectId
    path: str
    content: str


class FileGraphsResponseModel(BaseModel):
    repo_id: PydanticObjectId
    file_id: PydanticObjectId
    ast: dict[str, Any] | None
    cfg: list[ScopedGraphModel]
    dfg: list[ScopedGraphModel]
