"""Chat router.

This module defines the API endpoints related to chat.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from beanie import PydanticObjectId
from fastapi import APIRouter, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.auth.dependencies import CurrentUser
from app.chat.dependencies import ChatRepoAccess, VerifiedChatThread
from app.chat.llm import close_agent_resources, init_agent_resources
from app.chat.schemas import (
    ChatMessageRequest,
    ChatThreadListResponse,
    ChatThreadMessagesResponse,
    ChatThreadResponse,
    CreateChatRequest,
    UpdateChatTitleRequest,
)
from app.chat.service import (
    create_chat_stream,
    delete_thread,
    get_thread_messages,
    get_user_threads,
    resume_chat_stream,
    update_thread_title,
)
from app.chat.vectorstore import close_vectorstore, init_vectorstore


@asynccontextmanager
async def chat_lifespan(_: APIRouter) -> AsyncGenerator[Any, Any]:
    init_agent_resources()
    init_vectorstore()
    yield
    await close_vectorstore()
    close_agent_resources()


chat_router = APIRouter(prefix="/chat", tags=["chat"], lifespan=chat_lifespan)


@chat_router.get("", response_model=ChatThreadListResponse)
async def get_threads_endpoint(
    user: CurrentUser,
    repo_id: PydanticObjectId | None = None,
    search: str | None = None,
) -> ChatThreadListResponse:
    """Get all chat threads for the authenticated user.

    Threads are returned in descending order of `updated_at`.
    Optionally filtered by `repo_id` and/or `search` (case-insensitive
    substring match on title).

    Args:
        repo_id(PydanticObjectId | None): Optional repository ID to filter by.
        search(str | None): Optional search string to filter threads by title.

    Returns:
        ChatThreadListResponse: A list of the user's chat threads.
    """
    threads = await get_user_threads(user, repo_id, search)

    return ChatThreadListResponse(
        threads=[
            ChatThreadResponse(
                chat_id=thread.id,  # type: ignore[arg-type]
                repo_id=thread.repo_id,
                title=thread.title,
                created_at=thread.created_at,
                updated_at=thread.updated_at,
            )
            for thread in threads
        ]
    )


@chat_router.post("", response_class=EventSourceResponse)
async def create_chat_endpoint(
    request: CreateChatRequest,
    user: CurrentUser,
    _access: ChatRepoAccess,
) -> AsyncGenerator[ServerSentEvent, None]:
    """Create a new chat thread and stream the first response via SSE.

    Args:
        request(CreateChatRequest): The chat request containing `repo_id`
            and the user's message.
        user(CurrentUser): The authenticated user (injected by FastAPI).
        _access(ChatRepoAccess): Dependency to verify the user has access
            to the repo (injected by FastAPI).

    Returns:
        EventSourceResponse: An SSE stream of chat events.
    """
    async for event in create_chat_stream(request.repo_id, request.message, user):
        yield event


@chat_router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_endpoint(
    thread: VerifiedChatThread,
) -> None:
    """Delete a chat thread and all its associated data.

    Removes the checkpointer data (checkpoint and checkpoint_writes
    collections) and the ChatThreadDocument from MongoDB.

    Args:
        thread(VerifiedChatThread): The verified chat thread document
            (injected by FastAPI).
    """
    await delete_thread(thread)


@chat_router.get(
    "/{chat_id}",
    response_model=ChatThreadMessagesResponse,
    response_model_exclude_none=True,
)
async def get_chat_messages_endpoint(
    thread: VerifiedChatThread,
) -> ChatThreadMessagesResponse:
    """Retrieve the conversation history for a chat thread.

    Returns all user and assistant messages in chronological order.
    System messages are excluded.

    Args:
        thread(VerifiedChatThread): The verified chat thread document
            (injected by FastAPI).

    Returns:
        ChatThreadMessagesResponse: The thread's message history.
    """
    messages = await get_thread_messages(thread)

    return ChatThreadMessagesResponse(
        chat_id=thread.id,  # type: ignore
        messages=messages,
    )


@chat_router.patch("/{chat_id}", response_model=ChatThreadResponse)
async def update_chat_title_endpoint(
    request: UpdateChatTitleRequest,
    thread: VerifiedChatThread,
) -> ChatThreadResponse:
    """Update the title of an existing chat thread.

    Args:
        request(UpdateChatTitleRequest): The request containing the new title.
        thread(VerifiedChatThread): The verified chat thread document
            (injected by FastAPI).

    Returns:
        ChatThreadResponse: The updated chat thread.
    """
    updated = await update_thread_title(thread, request.title)

    return ChatThreadResponse(
        chat_id=updated.id,  # type: ignore[arg-type]
        repo_id=updated.repo_id,
        title=updated.title,
        created_at=updated.created_at,
        updated_at=updated.updated_at,
    )


@chat_router.post("/{chat_id}", response_class=EventSourceResponse)
async def resume_chat_endpoint(
    request: ChatMessageRequest,
    user: CurrentUser,
    thread: VerifiedChatThread,
) -> AsyncGenerator[ServerSentEvent, None]:
    """Resume an existing chat thread and stream the response via SSE.

    Args:
        request(ChatMessageRequest): The chat request containing the
            user's message.
        user(CurrentUser): The authenticated user (injected by FastAPI).
        thread(VerifiedChatThread): The verified chat thread document
            (injected by FastAPI).

    Returns:
        EventSourceResponse: An SSE stream of chat events.
    """
    async for event in resume_chat_stream(thread, request.message, user):
        yield event
