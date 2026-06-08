"""Documentation Object Document Models (ODM).

This module defines the ODM related to Documentation.
"""

from beanie import Document, PydanticObjectId
from pymongo import IndexModel


class FileDocumentationDocument(Document):
    repo_id: PydanticObjectId
    file_id: PydanticObjectId
    content: str

    class Settings:
        name = "file_documentations"
        indexes = [
            IndexModel([("repo_id", 1), ("file_id", 1)], unique=True),
        ]


class MetaRepoDocument(Document):
    repo_id: PydanticObjectId
    content: str

    class Settings:
        name = "meta_repositories"
        indexes = [
            IndexModel([("repo_id", 1)], unique=True),
        ]
