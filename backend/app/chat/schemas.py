"""Chat Pydantic schemas.

This module defines the schemas for the chat endpoints (e.g., Data Transfer Object - DTO).
"""

from datetime import datetime

from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from app.chat.config import chat_settings


class ChatContext(BaseModel):
    username: str | None


class CreateChatRequest(BaseModel):
    repo_id: PydanticObjectId
    message: str


class ChatMessageRequest(BaseModel):
    message: str


class ChatThreadResponse(BaseModel):
    chat_id: PydanticObjectId
    repo_id: PydanticObjectId
    title: str
    created_at: datetime
    updated_at: datetime


class ChatThreadListResponse(BaseModel):
    threads: list[ChatThreadResponse]


class ChatMessageResponse(BaseModel):
    """A stored chat message as returned by the API.

    The variant fields are populated only for an assistant message that has
    been regenerated, so an unbranched thread's payload is unchanged.
    """

    id: PydanticObjectId
    role: str
    content: str
    sources: list[dict[str, str]] | None = None

    variant_index: int | None = None
    """The 1-based position of this answer among its siblings."""

    variant_count: int | None = None
    prev_variant_id: PydanticObjectId | None = None
    next_variant_id: PydanticObjectId | None = None


class ChatThreadMessagesResponse(BaseModel):
    chat_id: PydanticObjectId
    messages: list[ChatMessageResponse]
    next_cursor: PydanticObjectId | None = None


class RetryMessageRequest(BaseModel):
    message_id: PydanticObjectId


class SelectVariantRequest(BaseModel):
    message_id: PydanticObjectId


class UpdateChatTitleRequest(BaseModel):
    title: str = Field(min_length=1, max_length=chat_settings.TITLE_MAX_LEN)
