"""Pipeline schemas.

This module defines the schemas for the pipeline (e.g., Data Transfer Object - DTO).
"""

from typing import TypedDict

from pydantic import BaseModel, Field


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
    blob_path: str
    languages: set[str] = Field(default_factory=set)
    repo_id: str
    pipeline_run_id: str
