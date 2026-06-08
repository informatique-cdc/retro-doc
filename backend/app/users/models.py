"""Users Object Document Models (ODM).

This module defines the ODM related to Users.
"""

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel


class UserRepoDocument(Document):
    name: str
    user_id: str
    repo_id: PydanticObjectId
    color: str | None = Field(default=None, max_length=50)

    class Settings:
        name = "user_repos"
        keep_nulls = False
        indexes = [
            IndexModel([("user_id", 1), ("repo_id", 1)], unique=True),
        ]
