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
    role: str
    content: str
    sources: list[dict[str, str]] | None = None


class ChatThreadMessagesResponse(BaseModel):
    chat_id: PydanticObjectId
    messages: list[ChatMessageResponse]


class UpdateChatTitleRequest(BaseModel):
    title: str = Field(min_length=1, max_length=chat_settings.TITLE_MAX_LEN)
