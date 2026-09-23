"""Pipeline schemas.

This module defines the schemas for the pipeline (e.g., Data Transfer Object - DTO).
"""

from typing import Literal, Self, TypedDict

from pydantic import BaseModel, Field, model_validator


class FileResult(TypedDict):
    file_id: str
    file_persisted: bool
    ast_persisted: bool
    cfg_persisted: int
    cfg_failed: int
    cfg_built: bool
    dfg_persisted: int
    dfg_failed: int
    dfg_built: bool
    doc_persisted: bool
    rag_persisted: bool


class PipelineRequest(BaseModel):
    source: Literal["zip", "git"]
    blob_path: str
    languages: set[str] = Field(default_factory=set)
    repo_id: str
    instance_id: str
    """The PipelineRunDocument id, which also becomes this orchestration's instance id."""

    # Git source only
    repo_url: str | None = None
    commit: str | None = None
    ref: str | None = None
    token: str | None = None

    @model_validator(mode="after")
    def _require_repo_url_for_git(self) -> Self:
        if self.source == "git" and self.repo_url is None:
            raise ValueError("repo_url is required when source is 'git'")
        return self
