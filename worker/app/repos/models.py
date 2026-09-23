"""Repos Object Document Models (ODM).

This module defines the ODM related to Repositories.
"""

from datetime import UTC, datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel


class RepoDocument(Document):
    repo_url: str
    repo_hash: str | None = None
    analyzer_version: str
    ran_analyzer_version: str | None = None
    blob_path: str
    user_count: int = 1
    languages: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "repos"
        keep_nulls = False


class FileDocument(Document):
    repo_id: PydanticObjectId
    path: str

    class Settings:
        name = "files"
        indexes = [
            IndexModel([("repo_id", 1), ("path", 1)], unique=True),
        ]
