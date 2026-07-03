"""Graphs Object Document Models (ODM).

This module defines the ODM related to graphs.
"""

from typing import Any

from beanie import Document, PydanticObjectId
from pydantic import BaseModel
from pymongo import IndexModel


class BaseFields(BaseModel):
    repo_id: PydanticObjectId
    file_id: PydanticObjectId
    content: dict[str, Any]


class ScopedFields(BaseFields):
    scope: str | None


class ASTDocument(BaseFields, Document):
    class Settings:
        name = "ast_graphs"
        indexes = [
            IndexModel([("repo_id", 1), ("file_id", 1)], unique=True),
        ]


class CFGDocument(ScopedFields, Document):
    class Settings:
        name = "cfg_graphs"
        keep_nulls = False
        indexes = [
            IndexModel([("repo_id", 1), ("file_id", 1), ("scope", 1)], unique=True),
        ]


class DFGDocument(ScopedFields, Document):
    class Settings:
        name = "dfg_graphs"
        keep_nulls = False
        indexes = [
            IndexModel([("repo_id", 1), ("file_id", 1), ("scope", 1)], unique=True),
        ]
