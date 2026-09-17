"""Chat Object Document Models (ODM).

This module defines the ODM related to Chat.
"""

from datetime import UTC, datetime
from typing import Any, Literal

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import IndexModel

from app.chat.config import chat_settings

SELECTED_BRANCH_FILTER: dict[str, Any] = {"active": True}
"""Scopes a message query to the thread's currently selected branch."""


class ChatThreadDocument(Document):
    user_id: str
    repo_id: PydanticObjectId
    title: str = Field(max_length=chat_settings.TITLE_MAX_LEN)
    has_variants: bool = False
    """Whether any turn in this thread has been regenerated.

    Purely an optimisation: it lets the read paths skip the sibling
    lookups entirely for threads that have never branched, which is the
    overwhelming majority of them.
    """
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
    input_checkpoint_id: str | None = None
    """The checkpoint ID of the input that led to this message.

    Only needed to regenerate the first turn of a thread, which has no
    earlier resting checkpoint to fork from.
    """
    role: Literal["human", "ai"]
    content: str
    sources: list[dict[str, str]] | None = None
    active: bool = True
    """Whether this message lies on the thread's currently selected branch."""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "chat_messages"
        keep_nulls = False
        indexes = [
            IndexModel([("thread_id", 1), ("created_at", 1)]),
            IndexModel([("thread_id", 1), ("active", 1)]),
            IndexModel([("thread_id", 1), ("parent_checkpoint_id", 1)]),
            IndexModel([("checkpoint_id", 1)]),
        ]


class ChatMessageView(BaseModel):
    """A `ChatMessageDocument` projected down to its branch structure.

    Walking the message tree only needs the links between messages, and
    conversations get long, so `content` is deliberately left out: it is by
    far the largest field and none of the branch logic reads it.
    """

    id: PydanticObjectId = Field(alias="_id")
    role: Literal["human", "ai"]
    checkpoint_id: str | None = None
    parent_checkpoint_id: str | None = None
    active: bool = True
