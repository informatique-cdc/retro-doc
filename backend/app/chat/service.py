"""Chat service.

This module contains the business logic for the chat.
"""

import asyncio
import re
from collections.abc import AsyncGenerator, AsyncIterable
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from fastapi.sse import ServerSentEvent
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableConfig
from loguru import logger

from app.auth.schemas import User
from app.chat.config import chat_settings
from app.chat.llm import get_agent, title_model
from app.chat.models import ChatMessageDocument, ChatThreadDocument
from app.chat.prompts import TITLE_SYSTEM_PROMPT
from app.chat.schemas import ChatContext, ChatMessageResponse
from app.chat.sse import (
    sse_chat_id,
    sse_done,
    sse_error,
    sse_title,
    sse_token,
    sse_tool_end,
    sse_tool_start,
)


def _copy_config_with_checkpoint_id(
    config: RunnableConfig, checkpoint_id: str
) -> RunnableConfig:
    """Create a copy of config with the given checkpoint_id set.

    Args:
        config(RunnableConfig): The original runnable config.
        checkpoint_id(str): The checkpoint ID to set in the config.

    Returns:
        RunnableConfig: A new runnable config with the checkpoint_id included
            in the "configurable" section.
    """
    return {
        **config,
        "configurable": {
            **config.get("configurable", {}),
            "checkpoint_id": checkpoint_id,
        },
    }


def _deduplicate_sources(
    sources: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Remove duplicate sources by file path, keeping first occurrence.

    Args:
        sources(list[dict[str, str]]): A list of source dictionaries, each
            containing at least a "path" key.

    Returns:
        list[dict[str, str]]: A new list of sources with duplicates removed,
            preserving the original order.
    """
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for src in sources:
        if src["path"] not in seen:
            seen.add(src["path"])
            unique.append(src)
    return unique


async def _persist_turn(
    thread_id: str,
    message: str,
    response: str,
    checkpoint_id: str | None,
    parent_checkpoint_id: str | None,
    sources: list[dict[str, str]] | None = None,
) -> None:
    """Persist the human message and AI response to MongoDB.

    Saves both messages as `ChatMessageDocument` records using the
    checkpoint IDs captured from the stream's checkpoint events.

    The `checkpoint_id` is the resting checkpoint of this turn (the
    checkpoint where `next` is empty). The `parent_checkpoint_id`
    is the resting checkpoint of the *previous* turn, or `None` for
    the first turn in a thread. Two turns that branch from the same
    point share the same `parent_checkpoint_id`.

    Args:
        thread_id(str): The thread ID string.
        message(str): The user's message content.
        response(str): The full AI response content.
        checkpoint_id(str): Resting checkpoint ID from the stream.
        parent_checkpoint_id(str | None): Previous turn's resting
            checkpoint ID, or `None` for the first turn.
        sources(list[dict[str, str]] | None): Optional file references
            produced by tools during the response generation.
    """
    thread_oid = PydanticObjectId(thread_id)
    await ChatMessageDocument(
        thread_id=thread_oid,
        checkpoint_id=checkpoint_id,
        parent_checkpoint_id=parent_checkpoint_id,
        role="human",
        content=message,
    ).insert()
    await ChatMessageDocument(
        thread_id=thread_oid,
        checkpoint_id=checkpoint_id,
        parent_checkpoint_id=parent_checkpoint_id,
        role="ai",
        content=response,
        sources=sources,
    ).insert()


async def _prepare_safe_stream(
    agent: Any,
    config: RunnableConfig,
) -> tuple[RunnableConfig, str | None]:
    """Prepare a runnable config for safe streaming by ensuring it
    starts from a resting checkpoint.

    If the provided config's checkpoint has a `next`, it means it's
    a stale checkpoint that was branched from. In that case, we need
    to find the last input checkpoint in the history to get the correct
    lineage and avoid streaming from a stale branch.

    3 cases:
        1. Config has no `checkpoint_id`: safe to stream from directly.
        2. Config's checkpoint has no next: it's a resting checkpoint,
            safe to stream from directly.
        3. Config's checkpoint has a next: it's a stale checkpoint, need
            to find the last input checkpoint in the history and stream
            from there.

    Args:
        agent(Any): The agent instance to query for state and history.
        config(RunnableConfig): The initial runnable config, which may or
            may not contain a `checkpoint_id`.

    Returns:
        tuple[RunnableConfig, str | None]: A tuple containing the
            potentially modified runnable config that is safe for streaming,
            and the parent checkpoint ID if available (used for history tracking).

    Raises:
        RuntimeError: If the checkpoint is stale but no input checkpoint is
            found in the history, which indicates a corrupted graph state.
    """
    state = await agent.aget_state(config)

    parent_checkpoint_id = state.config["configurable"].get("checkpoint_id") or None
    if parent_checkpoint_id is not None:
        config = _copy_config_with_checkpoint_id(config, parent_checkpoint_id)

    # If the resting checkpoint has no next, it's safe to stream from directly.
    if not state.next:
        return config, parent_checkpoint_id

    # If the resting checkpoint has a next, it means it's a stale checkpoint
    # that was branched from. We need to find the last input checkpoint in the
    # history to get the correct lineage and avoid streaming from a stale branch.
    if state.metadata["source"] != "input":
        history = [
            s
            async for s in agent.aget_state_history(
                config, filter={"source": "input"}, before=config, limit=1
            )
        ]
        if not history:
            raise RuntimeError(
                "Stale graph detected but no checkpoint with source `input` found in history"
            )
        state = history[0]

    # Walk back to the oldest consecutive "input" checkpoint.
    last_input_state = state
    while state.parent_config is not None and state.metadata["source"] == "input":
        last_input_state = state
        state = await agent.aget_state(state.parent_config)
    if state.metadata["source"] == "input":
        last_input_state = state

    parent_id = (
        None
        if state.parent_config is None
        else state.config["configurable"]["checkpoint_id"]
    )
    return _copy_config_with_checkpoint_id(
        config, last_input_state.config["configurable"]["checkpoint_id"]
    ), parent_id


def _truncate_title(message: str) -> str:
    """Truncate a user message to use as a fallback title.

    Args:
        message(str): The user's original message.

    Returns:
        str: The message truncated to `chat_settings.TITLE_MAX_LEN`
            characters on a word boundary, with `...` appended if
            truncated.
    """
    if len(message) <= chat_settings.TITLE_MAX_LEN:
        return message
    return message[: chat_settings.TITLE_MAX_LEN - 3].rsplit(" ", 1)[0] + "..."


async def create_chat_stream(
    repo_id: PydanticObjectId, message: str, user: User
) -> AsyncGenerator[ServerSentEvent, None]:
    """Create a new chat thread and stream the response as SSE events.

    Emits the new thread ID as the first event, followed by the LLM
    response tokens, then the generated title, and finally the done
    sentinel. Title generation runs concurrently with the agent stream
    to avoid added latency.

    If thread creation fails, emits an SSE error event instead.

    Args:
        repo_id(PydanticObjectId): The repository ID to associate the
            thread with.
        message(str): The user's initial chat message.
        user(User): The authenticated user.

    Yields:
        ServerSentEvent: SSE-formatted events (chat_id, token, title,
            error, done).
    """
    try:
        thread = await create_thread(repo_id, user, message)
    except Exception:
        logger.exception("Chat: Failed to create chat thread.")
        yield sse_error("Failed to create chat thread.")
        yield sse_done()
        return

    thread_id = str(thread.id)

    yield sse_chat_id(thread_id)

    title_task = asyncio.create_task(generate_title(thread, message))

    async for event in stream_agent_response(message, thread_id, str(repo_id), user):
        yield event

    title = await title_task
    yield sse_title(title)
    yield sse_done()


async def create_thread(
    repo_id: PydanticObjectId, user: User, message: str
) -> ChatThreadDocument:
    """Create a new chat thread for a user and repo.

    The thread title is immediately set to a truncated version of the
    user's first message so the thread is never stored without a title.
    The title may later be overwritten by `generate_title` if the LLM
    call succeeds.

    Args:
        repo_id(PydanticObjectId): The repository ID to associate the thread with.
        user(User): The authenticated user.
        message(str): The user's first message, used as the default title.

    Returns:
        ChatThreadDocument: The newly created ChatThreadDocument.
    """
    thread = ChatThreadDocument(
        user_id=user.uid, repo_id=repo_id, title=_truncate_title(message)
    )
    await thread.insert()
    return thread


async def delete_thread(thread: ChatThreadDocument) -> None:
    """Delete a chat thread document.

    Only removes the ChatThreadDocument itself. Orphaned messages and
    checkpointer data should be cleaned in a separate job.

    Args:
        thread(ChatThreadDocument): The verified chat thread document to delete.
    """
    await thread.delete()


async def delete_threads_by_repo(user_id: str, repo_id: PydanticObjectId) -> None:
    """Bulk-delete all chat thread documents for a user in a specific repository.

    Only removes ChatThreadDocuments. Orphaned messages and checkpointer
    data should be cleaned in a separate job.

    Args:
        user_id(str): The user's unique identifier.
        repo_id(PydanticObjectId): The repository ID whose threads should be removed.
    """
    await ChatThreadDocument.find(
        ChatThreadDocument.user_id == user_id,
        ChatThreadDocument.repo_id == repo_id,
    ).delete()


async def generate_title(thread: ChatThreadDocument, message: str) -> str:
    """Generate a short title for a chat thread from the user's first message.

    Uses the chat model directly to generate a concise title. If the LLM
    call succeeds, the thread title is updated in the database. On failure,
    the existing title (set by `create_thread`) is kept as-is to avoid a
    redundant write.

    Args:
        thread(ChatThreadDocument): The chat thread document to update.
        message(str): The user's first message in the conversation.

    Returns:
        str: The generated title, or the existing thread title on failure.
    """
    try:
        response = await title_model.ainvoke(
            [
                {"role": "system", "content": TITLE_SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ]
        )
        title = _truncate_title(str(response.text).strip())
        thread.title = title
        await thread.save()
        return title
    except Exception:
        logger.exception("Chat: LLM title generation failed, using truncated message.")
        return thread.title


async def get_thread_messages(
    thread: ChatThreadDocument,
) -> list[ChatMessageResponse]:
    """Retrieve all conversation messages for a chat thread.

    Reads from the `ChatMessageDocument` collection, which stores
    the full unsummarized history independently of the LangGraph
    checkpointer.

    Args:
        thread(ChatThreadDocument): The verified chat thread document.

    Returns:
        list[ChatMessageResponse]: The conversation messages with role
            and content, in chronological order.
    """
    messages = (
        await ChatMessageDocument.find(ChatMessageDocument.thread_id == thread.id)
        .sort("+created_at")
        .to_list()
    )

    return [
        ChatMessageResponse(role=msg.role, content=msg.content, sources=msg.sources)
        for msg in messages
    ]


async def get_user_threads(
    user: User,
    repo_id: PydanticObjectId | None = None,
    search: str | None = None,
) -> list[ChatThreadDocument]:
    """Get all chat threads belonging to a user.

    Args:
        user(User): The authenticated user.
        repo_id(PydanticObjectId | None): Optional repository ID to filter by.
        search(str | None): Optional search string for case-insensitive
            substring match on the thread title.

    Returns:
        list[ChatThreadDocument]: A list of ChatThreadDocument instances.
    """
    query = ChatThreadDocument.find(ChatThreadDocument.user_id == user.uid)
    if repo_id is not None:
        query = query.find(ChatThreadDocument.repo_id == repo_id)
    if search is not None:
        query = query.find({"title": {"$regex": re.escape(search), "$options": "i"}})
    return await query.sort("-updated_at").to_list()


async def resume_chat_stream(
    thread: ChatThreadDocument, message: str, user: User
) -> AsyncGenerator[ServerSentEvent, None]:
    """Resume an existing chat thread and stream the response as SSE events.

    Updates the thread timestamp before streaming.

    Args:
        thread(ChatThreadDocument): The verified chat thread document.
        message(str): The user's chat message.
        user(User): The authenticated user.

    Yields:
        ServerSentEvent: SSE-formatted events (token, error, done).
    """
    await update_thread_timestamp(thread)
    async for event in stream_agent_response(
        message, str(thread.id), str(thread.repo_id), user
    ):
        yield event
    yield sse_done()


async def stream_agent_response(
    message: str, thread_id: str, repo_id: str, user: User
) -> AsyncIterable[ServerSentEvent]:
    """Stream agent response tokens as SSE-formatted strings.

    This generator yields only token and error events. The caller is
    responsible for emitting lifecycle events (`chat_id`, `title`,
    `done`). After the stream completes, both the user message and
    the full AI response are persisted to `ChatMessageDocument` with
    LangGraph checkpoint IDs for history tracking and branch support.

    Args:
        message(str): The user's chat message.
        thread_id(str): The LangGraph thread ID for conversation persistence.
        repo_id(str): The repository ID for scoping search results.
        user(User): The authenticated user, used to populate the chat context.

    Yields:
        ServerSentEvent: SSE-formatted events (token, error).
    """
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id, "repo_id": repo_id},
        "recursion_limit": chat_settings.AGENT_RECURSION_LIMIT,
    }
    agent = get_agent()
    response: str = ""
    response_sources: list[dict[str, str]] = []
    checkpoint_id: str | None = None

    try:
        (
            safe_config,
            parent_checkpoint_id,
        ) = await _prepare_safe_stream(agent, config)
        async for step in agent.astream(  # type: ignore[call-overload]
            {"messages": [{"role": "user", "content": message}]},
            config=safe_config,
            context=ChatContext(username=user.name),
            stream_mode=["checkpoints", "messages", "tasks"],
            version="v2",
        ):
            if step["type"] == "tasks":
                if step["data"]["name"] == "model":
                    if msgs := (step["data"].get("result") or {}).get("messages"):
                        response = str(msgs[0].text)
                elif step["data"]["name"] == "tools":
                    if inputs := step["data"].get("input"):
                        tool = inputs[0]["name"]
                        id = step["data"]["id"]
                        yield sse_tool_start(tool, id)
                    elif msgs := (step["data"].get("result") or {}).get("messages"):
                        msg = msgs[0]
                        tool = msg.name
                        sources = msg.artifact
                        status = msg.status
                        id = step["data"]["id"]
                        if sources:
                            response_sources.extend(sources)
                        yield sse_tool_end(tool, id, status, sources)
            elif step["type"] == "messages":
                msg, metadata = step["data"]
                if (
                    metadata.get("langgraph_node") == "model"
                    and isinstance(msg, AIMessageChunk)
                    and (token := msg.text)
                ):
                    yield sse_token(token)
            elif step["type"] == "checkpoints":
                data = step["data"]
                if data["config"]["configurable"][
                    "checkpoint_ns"
                ] == "" and not data.get("next"):
                    checkpoint_id = data["config"]["configurable"]["checkpoint_id"]

    except Exception:
        logger.exception("Chat: Chat stream failed.")
        yield sse_error("An error occurred while generating the response.")
        return

    try:
        await _persist_turn(
            thread_id=thread_id,
            message=message,
            response=response,
            checkpoint_id=checkpoint_id,
            parent_checkpoint_id=parent_checkpoint_id,
            sources=_deduplicate_sources(response_sources) or None,
        )
    except Exception:
        logger.exception("Chat: Failed to persist chat messages.")
        yield sse_error("Failed to save the conversation.")


async def update_thread_timestamp(thread: ChatThreadDocument) -> ChatThreadDocument:
    """Update the thread's `updated_at` field to the current time.

    Args:
        thread(ChatThreadDocument): The chat thread document to update.

    Returns:
        ChatThreadDocument: The updated chat thread document.
    """
    thread.updated_at = datetime.now(UTC)
    await thread.save()
    return thread


async def update_thread_title(
    thread: ChatThreadDocument, title: str
) -> ChatThreadDocument:
    """Update the title of a chat thread.

    Changing the title doesn't update the `updated_at` timestamp, since
    it's a metadata change rather than an update to the conversation
    content.

    Args:
        thread(ChatThreadDocument): The chat thread document to update.
        title(str): The new title for the thread.

    Returns:
        ChatThreadDocument: The updated chat thread document.
    """
    thread.title = title
    await thread.save()
    return thread
