"""Chat Object Document Models (ODM).

This module defines the ODM related to Chat.
"""

from datetime import UTC, datetime
from typing import Literal

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.chat.config import chat_settings


class ChatThreadDocument(Document):
    user_id: str
    repo_id: PydanticObjectId
    title: str = Field(max_length=chat_settings.TITLE_MAX_LEN)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "chat_threads"
        indexes = [
            IndexModel([("user_id", 1), ("updated_at", -1)]),
        ]


class ChatMessageDocument(Document):
    thread_id: PydanticObjectId
    checkpoint_id: str | None = None
    parent_checkpoint_id: str | None = None
    role: Literal["human", "ai"]
    content: str
    sources: list[dict[str, str]] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "chat_messages"
        keep_nulls = False
        indexes = [
            IndexModel([("thread_id", 1), ("created_at", 1)]),
            IndexModel([("checkpoint_id", 1)]),
        ]
