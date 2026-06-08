"""Repos Object Document Models (ODM).

This module defines the ODM related to Repositories.
"""

from datetime import datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.core.config import settings
from app.core.language_enum import Language


class RepoDocument(Document):
    repo_url: str | None = None
    repo_branch: str | None = None
    repo_hash: str | None = None
    blob_path: str
    user_count: int = 1
    language: Language
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(settings.APP_TIMEZONE)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(settings.APP_TIMEZONE)
    )

    class Settings:
        name = "repositories"


class FileDocument(Document):
    repo_id: PydanticObjectId
    path: str
    file_hash: str

    class Settings:
        name = "files"
        indexes = [
            IndexModel([("repo_id", 1), ("path", 1)], unique=True),
        ]
