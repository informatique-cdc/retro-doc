"""Documentation Object Document Models (ODM).

This module defines the ODM related to Documentation.
"""

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import IndexModel


class AnalysisStats(BaseModel):
    # Files detected in the uploaded archive
    files_detected: int = 0
    files_by_extension: dict[str, int] = Field(default_factory=dict)

    # File catalog
    file_success: int = 0
    file_failed: int = 0

    # Graph artifacts
    ast_success: int = 0
    ast_failed: int = 0
    cfg_success: int = 0
    cfg_failed: int = 0
    cfg_build_failed: int = 0
    dfg_success: int = 0
    dfg_failed: int = 0
    dfg_build_failed: int = 0

    # Documentation generation & RAG indexing
    doc_success: int = 0
    doc_failed: int = 0
    rag_success: int = 0
    rag_failed: int = 0


class FileDocumentationDocument(Document):
    repo_id: PydanticObjectId
    file_id: PydanticObjectId
    content: str

    class Settings:
        name = "file_documentations"
        indexes = [
            IndexModel([("repo_id", 1), ("file_id", 1)], unique=True),
        ]


class RepoMetaDocument(Document):
    repo_id: PydanticObjectId
    content: str
    stats: AnalysisStats

    class Settings:
        name = "repo_metas"
        indexes = [
            IndexModel([("repo_id", 1)], unique=True),
        ]
