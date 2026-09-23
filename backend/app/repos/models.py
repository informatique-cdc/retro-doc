"""Repos Object Document Models (ODM).

This module defines the ODM related to Repositories.
"""

from datetime import UTC, datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel


class RepoDocument(Document):
    """A repository's identity, source location and analyzer provenance.

    `user_count` is an upper-bound approximation of how many users hold this
    repository, never an exact count. The `UserRepoDocument` links are the real
    list. The counter is the fast filter that the deduplication lookups
    (`_find_live_git_repo`, `_find_live_zip_repo`) and both partial unique
    indexes below query on. The two are separate documents with no
    transaction between them, so every writer is ordered to make a crash leave
    the counter too high rather than too low — links are claimed before they
    are written and released after they are removed.
    """

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
        indexes = [
            IndexModel(
                [("repo_url", 1), ("repo_hash", 1), ("analyzer_version", 1)],
                partialFilterExpression={
                    "repo_hash": {"$type": "string"},
                    "user_count": {"$gt": 0},
                },
                unique=True,
            ),
            IndexModel(
                [("blob_path", 1), ("analyzer_version", 1)],
                partialFilterExpression={"user_count": {"$gt": 0}},
                unique=True,
            ),
        ]


class FileDocument(Document):
    repo_id: PydanticObjectId
    path: str

    class Settings:
        name = "files"
        indexes = [
            IndexModel([("repo_id", 1), ("path", 1)], unique=True),
        ]
