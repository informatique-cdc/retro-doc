"""Chat router.

This module defines the API endpoints related to chat.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from beanie import PydanticObjectId
from fastapi import APIRouter, Query, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.auth.dependencies import CurrentUser
from app.chat.config import chat_settings
from app.chat.dependencies import (
    ChatRepoAccess,
    VerifiedChatThread,
    VerifiedRetryTarget,
)
from app.chat.llm import close_agent_resources, init_agent_resources
from app.chat.schemas import (
    ChatMessageRequest,
    ChatThreadListResponse,
    ChatThreadMessagesResponse,
    ChatThreadResponse,
    CreateChatRequest,
    SelectVariantRequest,
    UpdateChatTitleRequest,
)
from app.chat.service import (
    create_chat_stream,
    delete_thread,
    get_thread_messages,
    get_user_threads,
    resume_chat_stream,
    retry_message_stream,
    select_variant,
    update_thread_title,
)
from app.chat.utils import to_message_response
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
    limit: int = Query(
        default=chat_settings.MESSAGES_PAGE_SIZE,
        ge=1,
        le=chat_settings.MESSAGES_MAX_PAGE_SIZE,
    ),
    before: PydanticObjectId | None = None,
) -> ChatThreadMessagesResponse:
    """Retrieve one page of the conversation history for a chat thread.

    Returns the most recent `limit` user and assistant messages in
    chronological order. System messages are excluded. Paging runs from
    newest to oldest: pass the previous response's `next_cursor` as
    `before` to fetch the next page, one step further into the past.

    Args:
        thread(VerifiedChatThread): The verified chat thread document
            (injected by FastAPI).
        limit(int): Maximum number of messages to return.
        before(PydanticObjectId | None): Optional message ID to page back
            from. Only messages strictly older than it are returned.

    Returns:
        ChatThreadMessagesResponse: A page of the thread's message history,
            with the `next_cursor` to pass as `before` on the next call.
            The cursor is absent once the history is exhausted.
    """
    messages, variants, next_cursor = await get_thread_messages(thread, limit, before)

    return ChatThreadMessagesResponse(
        chat_id=thread.id,  # type: ignore
        messages=[
            to_message_response(msg, variants.get(msg.id))  # type: ignore[arg-type]
            for msg in messages
        ],
        next_cursor=next_cursor,
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


@chat_router.post("/{chat_id}/retry", response_class=EventSourceResponse)
async def retry_chat_message_endpoint(
    user: CurrentUser,
    thread: VerifiedChatThread,
    retry_target: VerifiedRetryTarget,
) -> AsyncGenerator[ServerSentEvent, None]:
    """Generate another answer to an already-answered question.

    The new answer is stored alongside the existing one rather than
    replacing it, and becomes the one the conversation continues from.
    Regenerating an answer that was already followed up takes those
    follow-ups out of the conversation too. Selecting the old answer again
    brings them back. The answer must be on the conversation as it currently
    reads, which `VerifiedRetryTarget` checks before this stream opens.

    Args:
        user(CurrentUser): The authenticated user (injected by FastAPI).
        thread(VerifiedChatThread): The verified chat thread document
            (injected by FastAPI).
        retry_target(VerifiedRetryTarget): The answer to regenerate and the
            question it answers (injected by FastAPI).

    Returns:
        EventSourceResponse: An SSE stream of chat events.
    """
    target, prompt = retry_target

    async for event in retry_message_stream(thread, target, prompt, user):
        yield event


@chat_router.post(
    "/{chat_id}/variant",
    response_model=ChatThreadMessagesResponse,
    response_model_exclude_none=True,
)
async def select_chat_variant_endpoint(
    request: SelectVariantRequest,
    thread: VerifiedChatThread,
    limit: int = Query(
        default=chat_settings.MESSAGES_PAGE_SIZE,
        ge=1,
        le=chat_settings.MESSAGES_MAX_PAGE_SIZE,
    ),
) -> ChatThreadMessagesResponse:
    """Switch the conversation onto another answer to the same question.

    The answers that were not chosen, and the turns that followed them, are
    hidden rather than deleted: each answer keeps its own follow-ups, and
    switching back restores them.

    Since this changes the whole conversation from that point on, the newest
    page is returned so the caller can render the result directly.

    Args:
        request(SelectVariantRequest): The request containing the ID of the
            answer to switch to.
        thread(VerifiedChatThread): The verified chat thread document
            (injected by FastAPI).
        limit(int): Maximum number of messages to return.

    Returns:
        ChatThreadMessagesResponse: The newest page of the conversation as it
            now reads, with the `next_cursor` to pass as `before` to request
            the preceding page.
    """
    messages, variants, next_cursor = await select_variant(
        thread, request.message_id, limit
    )

    return ChatThreadMessagesResponse(
        chat_id=thread.id,  # type: ignore[arg-type]
        messages=[
            to_message_response(msg, variants.get(msg.id))  # type: ignore[arg-type]
            for msg in messages
        ],
        next_cursor=next_cursor,
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
