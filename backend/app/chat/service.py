"""Chat service.

This module contains the business logic for the chat.
"""

import asyncio
import re
from collections import defaultdict
from collections.abc import AsyncGenerator, AsyncIterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from fastapi import HTTPException, status
from fastapi.sse import ServerSentEvent
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableConfig
from loguru import logger

from app.auth.schemas import User
from app.chat.config import chat_settings
from app.chat.llm import get_agent, title_model
from app.chat.models import (
    SELECTED_BRANCH_FILTER,
    ChatMessageDocument,
    ChatMessageView,
    ChatThreadDocument,
)
from app.chat.prompts import TITLE_SYSTEM_PROMPT
from app.chat.schemas import ChatContext
from app.chat.sse import (
    sse_chat_id,
    sse_done,
    sse_error,
    sse_message_saved,
    sse_title,
    sse_token,
    sse_tool_end,
    sse_tool_start,
)


@dataclass(frozen=True, slots=True)
class VariantPosition:
    """One answer's place among the other answers to the same question."""

    index: int
    """1-based, for display as `index/count`."""

    count: int
    prev_id: PydanticObjectId | None
    next_id: PydanticObjectId | None


@dataclass(frozen=True, slots=True)
class ForkPoint:
    """Where to re-enter the graph when regenerating an answer."""

    checkpoint_id: str
    """The checkpoint to resume the graph from."""

    parent_checkpoint_id: str | None
    """Persisted verbatim on the new message, so it joins the right sibling group."""

    replay: bool = False
    """Replay the pending model task instead of sending the message again.

    Used for the first turn of a thread, which has no earlier resting
    checkpoint to fork from, so its `{"source": "input"}` checkpoint is
    replayed instead.
    """

    supersedes: PydanticObjectId | None = None
    """The answer this run regenerates, deactivated once the new one is saved."""

    input_checkpoint_id: str | None = None
    """Carried over to the new message when replaying, since no new one is written."""


async def _active_leaf_checkpoint_id(thread_id: PydanticObjectId) -> str | None:
    """Find the resting checkpoint the selected branch currently ends on.

    Args:
        thread_id(PydanticObjectId): The thread to inspect.

    Returns:
        str | None: The `checkpoint_id` of the newest active AI message,
            or `None` if the thread has no answer to continue from.
    """
    leaf = (
        await ChatMessageDocument.find(
            ChatMessageDocument.thread_id == thread_id,
            {"role": "ai"},
            SELECTED_BRANCH_FILTER,
        )
        .project(ChatMessageView)
        .sort("-_id")
        .limit(1)
        .to_list()
    )
    return leaf[0].checkpoint_id if leaf else None


async def _annotate_variants(
    thread_id: PydanticObjectId, messages: list[ChatMessageDocument]
) -> dict[PydanticObjectId, VariantPosition]:
    """Build the variant pager metadata for a page of messages.

    Every AI message on the page is looked up against its siblings — the
    answers sharing its `parent_checkpoint_id` — in a single query. Turns
    that were never regenerated have no siblings and are left out, so the
    response stays exactly as it was before regeneration existed.

    Args:
        thread_id(PydanticObjectId): The thread the page belongs to.
        messages(list[ChatMessageDocument]): The page of messages.

    Returns:
        dict[PydanticObjectId, VariantPosition]: The pager metadata keyed by
            message ID, for messages that have more than one answer.
    """
    parents = {msg.parent_checkpoint_id for msg in messages if msg.role == "ai"}
    if not parents:
        return {}

    siblings = (
        await ChatMessageDocument.find(
            ChatMessageDocument.thread_id == thread_id,
            {"role": "ai", "parent_checkpoint_id": {"$in": list(parents)}},
        )
        .project(ChatMessageView)
        .to_list()
    )

    groups: dict[str | None, list[ChatMessageView]] = defaultdict(list)
    for sibling in siblings:
        groups[sibling.parent_checkpoint_id].append(sibling)

    annotations: dict[PydanticObjectId, VariantPosition] = {}
    for group in groups.values():
        annotations.update(_variant_positions(group))
    return annotations


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


async def _load_thread_nodes(
    thread_id: PydanticObjectId,
) -> dict[str | None, list[ChatMessageView]]:
    """Load a thread's messages as a tree, grouped by the turn they follow.

    Messages sharing a `parent_checkpoint_id` are the alternatives at that
    point in the conversation: the question asked there, and every answer
    given to it. Each group is ordered by ID, so the first entry is the
    question and the answers follow in the order they were generated.

    Args:
        thread_id(PydanticObjectId): The thread to load.

    Returns:
        dict[str | None, list[ChatMessageView]]: The messages keyed by
            the checkpoint they branch from, `None` for the first turn.
    """
    nodes = (
        await ChatMessageDocument.find(ChatMessageDocument.thread_id == thread_id)
        .project(ChatMessageView)
        .to_list()
    )
    by_parent: dict[str | None, list[ChatMessageView]] = defaultdict(list)
    for node in nodes:
        by_parent[node.parent_checkpoint_id].append(node)
    for group in by_parent.values():
        group.sort(key=lambda node: node.id)
    return by_parent


async def _persist_turn(
    thread_id: str,
    message: str,
    response: str,
    checkpoint_id: str | None,
    parent_checkpoint_id: str | None,
    sources: list[dict[str, str]] | None = None,
    input_checkpoint_id: str | None = None,
    persist_human: bool = True,
) -> tuple[ChatMessageDocument | None, ChatMessageDocument]:
    """Persist the human message and AI response to MongoDB.

    Saves both messages as `ChatMessageDocument` records using the
    checkpoint IDs captured from the stream's checkpoint events.

    The `checkpoint_id` is the resting checkpoint of this turn (the
    checkpoint where `next` is empty). The `parent_checkpoint_id`
    is the resting checkpoint of the *previous* turn, or `None` for
    the first turn in a thread. Two turns that branch from the same
    point share the same `parent_checkpoint_id`.

    Regenerating an answer passes `persist_human=False`: the human message
    is not duplicated, so a turn is one human message with one or more AI
    answers hanging off it, which is also how the frontend renders it.

    Args:
        thread_id(str): The thread ID string.
        message(str): The user's message content.
        response(str): The full AI response content.
        checkpoint_id(str): Resting checkpoint ID from the stream.
        parent_checkpoint_id(str | None): Previous turn's resting
            checkpoint ID, or `None` for the first turn.
        sources(list[dict[str, str]] | None): Optional file references
            produced by tools during the response generation.
        input_checkpoint_id(str | None): The turn's `input` checkpoint,
            needed to regenerate the first answer of a thread.
        persist_human(bool): Whether to insert the human message. `False`
            when regenerating, since the original one is reused.

    Returns:
        tuple[ChatMessageDocument | None, ChatMessageDocument]: The stored
            human message (`None` when regenerating) and the stored AI message.
    """
    thread_oid = PydanticObjectId(thread_id)
    human: ChatMessageDocument | None = None
    if persist_human:
        human = await ChatMessageDocument(
            thread_id=thread_oid,
            checkpoint_id=checkpoint_id,
            parent_checkpoint_id=parent_checkpoint_id,
            input_checkpoint_id=input_checkpoint_id,
            role="human",
            content=message,
        ).insert()
    ai = await ChatMessageDocument(
        thread_id=thread_oid,
        checkpoint_id=checkpoint_id,
        parent_checkpoint_id=parent_checkpoint_id,
        input_checkpoint_id=input_checkpoint_id,
        role="ai",
        content=response,
        sources=sources,
    ).insert()
    return human, ai


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


async def _publish_turn(
    thread_id: str,
    human: ChatMessageDocument | None,
    ai: ChatMessageDocument,
    fork: ForkPoint | None,
) -> ServerSentEvent:
    """Report where a freshly stored answer landed in the thread.

    An ordinary turn is reported as-is. A regeneration also moves the
    selected branch onto the new answer, which happens only now that the
    replacement is safely stored, so a failed stream leaves the thread
    showing the answer it already had.

    Args:
        thread_id(str): The thread ID string.
        human(ChatMessageDocument | None): The stored human message, `None`
            when regenerating.
        ai(ChatMessageDocument): The stored AI message.
        fork(ForkPoint | None): The fork this run was generated from, `None`
            for an ordinary turn.

    Returns:
        ServerSentEvent: The `message_saved` event, carrying the answer's
            place among its siblings when it superseded one.
    """
    if fork is None or fork.supersedes is None:
        return sse_message_saved(
            str(ai.id),
            human_message_id=str(human.id) if human else None,
        )

    position = await _supersede(
        PydanticObjectId(thread_id), superseded=fork.supersedes, replacement=ai
    )
    return sse_message_saved(
        str(ai.id),
        variant_index=position.index,
        variant_count=position.count,
        prev_variant_id=str(position.prev_id) if position.prev_id else None,
    )


async def _resolve_entry_point(
    agent: Any,
    config: RunnableConfig,
    message: str,
    fork: ForkPoint | None,
) -> tuple[RunnableConfig, str | None, dict[str, Any] | None]:
    """Decide where the graph re-enters and what it is fed.

    An ordinary turn recovers a resting checkpoint to stream from and sends
    the message. A regeneration takes the fork point verbatim, and replays
    the pending model task instead of sending the message again when the
    fork has nothing earlier to resume from.

    Args:
        agent(Any): The agent instance to query for state and history.
        config(RunnableConfig): The runnable config for this turn.
        message(str): The user's chat message.
        fork(ForkPoint | None): Where to re-enter the graph when regenerating
            an answer. `None` for an ordinary turn.

    Returns:
        tuple[RunnableConfig, str | None, dict[str, Any] | None]: The config
            to stream with, the previous turn's resting checkpoint ID, and
            the graph input (`None` to replay).
    """
    if fork is None:
        (
            safe_config,
            parent_checkpoint_id,
        ) = await _prepare_safe_stream(agent, config)
        stream_input: dict[str, Any] | None = {
            "messages": [{"role": "user", "content": message}]
        }
    else:
        # A fork target is a resting checkpoint by construction, so
        # `_prepare_safe_stream` would pass it straight through. It is
        # skipped so the new answer inherits its sibling group verbatim
        # rather than whatever the graph resolves to.
        safe_config = _copy_config_with_checkpoint_id(config, fork.checkpoint_id)
        parent_checkpoint_id = fork.parent_checkpoint_id
        stream_input = (
            None
            if fork.replay
            else {"messages": [{"role": "user", "content": message}]}
        )
    return safe_config, parent_checkpoint_id, stream_input


async def _supersede(
    thread_id: PydanticObjectId,
    superseded: PydanticObjectId,
    replacement: ChatMessageDocument,
) -> VariantPosition:
    """Move the selected branch onto a freshly regenerated answer.

    Everything that grew out of the superseded answer leaves with it, having
    been written in reply to an answer the thread no longer gives. One range
    update covers them: a turn is always stored after the one it follows, so
    they all have a higher ID. The extra messages it catches are siblings and
    rival branches, already inactive. The question the answers belong to was
    stored earlier, below the range.

    Nothing is deleted: `select_variant` puts the superseded answer, and the
    turns that followed it, back on the conversation.

    Args:
        thread_id(PydanticObjectId): The thread being updated.
        superseded(PydanticObjectId): The answer being regenerated.
        replacement(ChatMessageDocument): The answer that replaces it.

    Returns:
        VariantPosition: The replacement's place among its siblings.
    """
    await ChatMessageDocument.find(
        ChatMessageDocument.thread_id == thread_id,
        {"_id": {"$gte": superseded, "$ne": replacement.id}},
    ).update({"$set": {"active": False}})

    await ChatThreadDocument.find_one(ChatThreadDocument.id == thread_id).update(
        {"$set": {"has_variants": True}}
    )

    siblings = (
        await ChatMessageDocument.find(
            ChatMessageDocument.thread_id == thread_id,
            {"role": "ai", "parent_checkpoint_id": replacement.parent_checkpoint_id},
        )
        .project(ChatMessageView)
        .to_list()
    )
    return _variant_positions(siblings).get(
        replacement.id,  # type: ignore[arg-type]
        VariantPosition(index=1, count=1, prev_id=None, next_id=None),
    )


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


def _variant_positions(
    group: list[ChatMessageView],
) -> dict[PydanticObjectId, VariantPosition]:
    """Number a group of sibling answers for the pager.

    Ordered by `_id`, so the numbering matches the order they were
    generated in and stays stable as more are added.

    Args:
        group(list[ChatMessageView]): Answers sharing a
            `parent_checkpoint_id`.

    Returns:
        dict[PydanticObjectId, VariantPosition]: Empty when the question was
            answered only once, since there is then nothing to page through.
    """
    if len(group) < 2:
        return {}
    ordered = sorted(group, key=lambda node: node.id)
    return {
        node.id: VariantPosition(
            index=position + 1,
            count=len(ordered),
            prev_id=ordered[position - 1].id if position else None,
            next_id=(ordered[position + 1].id if position + 1 < len(ordered) else None),
        )
        for position, node in enumerate(ordered)
    }


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
    limit: int,
    before: PydanticObjectId | None = None,
) -> tuple[
    list[ChatMessageDocument],
    dict[PydanticObjectId, VariantPosition],
    PydanticObjectId | None,
]:
    """Retrieve one page of conversation messages for a chat thread.

    Reads from the `ChatMessageDocument` collection, which stores
    the full unsummarized history independently of the LangGraph
    checkpointer.

    Pages backwards from the newest message: without `before` the most
    recent `limit` messages are returned, and each subsequent call passes
    the previous page's `next_cursor` to walk further into the past.
    The page itself is always ordered chronologically.

    The cursor is a keyset on `_id`, not on `created_at`. Message ids are
    driver-generated ObjectIds, so within a thread they are unique and
    ordered by insertion — which `created_at` is not, since `_persist_turn`
    writes the human and ai message of a turn back-to-back and they can
    share a timestamp. It also keeps this to a single-field sort.

    Only messages on the selected branch are returned. Answers the user
    regenerated away from, and the turns that followed them, are skipped —
    which keeps working with the `_id` keyset, since the selected branch is
    still an ordered subsequence of the thread.

    Args:
        thread(ChatThreadDocument): The verified chat thread document.
        limit(int): The maximum number of messages to return.
        before(PydanticObjectId | None): Optional ID of the message to page
            back from. Only messages strictly older than it are returned.

    Returns:
        tuple[list[ChatMessageDocument], dict[PydanticObjectId, VariantPosition],
            PydanticObjectId | None]: The page of messages in chronological
            order, the pager metadata of those answers that have siblings, and
            the cursor to pass as `before` to fetch the older page (`None` when
            the history is exhausted).

    Raises:
        HTTPException: 422 if `before` does not identify a message of this
            thread.
    """
    query = ChatMessageDocument.find(
        ChatMessageDocument.thread_id == thread.id, SELECTED_BRANCH_FILTER
    )

    if before is not None:
        anchor = await ChatMessageDocument.find_one(
            ChatMessageDocument.id == before,
            ChatMessageDocument.thread_id == thread.id,
        )
        if anchor is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid pagination cursor.",
            )
        query = query.find({"_id": {"$lt": anchor.id}})

    # Over-fetch by one to detect whether older messages remain.
    messages = await query.sort("-_id").limit(limit + 1).to_list()

    has_more = len(messages) > limit
    messages = messages[:limit]
    messages.reverse()

    next_cursor = messages[0].id if has_more and messages else None

    variants = (
        await _annotate_variants(thread.id, messages)  # type: ignore[arg-type]
        if thread.has_variants
        else {}
    )

    return messages, variants, next_cursor


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

    In a thread with regenerated answers the graph's most recent checkpoint
    can belong to a branch the user has since switched away from, so the
    turn is explicitly continued from the end of the selected branch.

    Args:
        thread(ChatThreadDocument): The verified chat thread document.
        message(str): The user's chat message.
        user(User): The authenticated user.

    Yields:
        ServerSentEvent: SSE-formatted events (token, error, done).
    """
    await update_thread_timestamp(thread)
    resume_checkpoint_id = (
        await _active_leaf_checkpoint_id(thread.id)  # type: ignore[arg-type]
        if thread.has_variants
        else None
    )
    async for event in stream_agent_response(
        message,
        str(thread.id),
        str(thread.repo_id),
        user,
        resume_checkpoint_id=resume_checkpoint_id,
    ):
        yield event
    yield sse_done()


async def retry_message_stream(
    thread: ChatThreadDocument,
    target: ChatMessageDocument,
    prompt: str,
    user: User,
) -> AsyncGenerator[ServerSentEvent, None]:
    """Generate another answer to an already-answered question.

    The graph is re-entered at the checkpoint the original answer was
    generated from, so the model sees the same conversation it saw the first
    time. Which checkpoint that is depends on where in the thread the answer
    sits:

    - Normally it is the resting checkpoint of the previous turn. That
      predates the question, so the question is sent again to reach the same
      state, and no second copy of it is stored.
    - The first answer of a thread has no previous turn. Its `input`
      checkpoint — which already holds the question — is replayed instead.

    Args:
        thread(ChatThreadDocument): The verified chat thread document.
        target(ChatMessageDocument): The answer being regenerated, as
            returned by `get_verified_retry_target`.
        prompt(str): The content of the question it answers.
        user(User): The authenticated user.

    Yields:
        ServerSentEvent: SSE-formatted events (token, tool, message_saved,
            error, done).
    """
    replay = target.parent_checkpoint_id is None
    checkpoint_id = (
        target.input_checkpoint_id if replay else target.parent_checkpoint_id
    )
    fork = ForkPoint(
        checkpoint_id=checkpoint_id,  # type: ignore[arg-type]
        parent_checkpoint_id=target.parent_checkpoint_id,
        replay=replay,
        supersedes=target.id,
        input_checkpoint_id=target.input_checkpoint_id if replay else None,
    )

    await update_thread_timestamp(thread)
    async for event in stream_agent_response(
        prompt, str(thread.id), str(thread.repo_id), user, fork=fork
    ):
        yield event
    yield sse_done()


async def select_variant(
    thread: ChatThreadDocument,
    message_id: PydanticObjectId,
    limit: int,
) -> tuple[
    list[ChatMessageDocument],
    dict[PydanticObjectId, VariantPosition],
    PydanticObjectId | None,
]:
    """Switch the conversation onto one of a question's other answers.

    The chosen answer, and the turns that followed it last time it was
    selected, become the conversation again. Everything belonging to the
    sibling answers is hidden. Follow-ups are not discarded — each answer
    keeps the conversation that grew out of it, and switching back restores
    it.

    Args:
        thread(ChatThreadDocument): The verified chat thread document.
        message_id(PydanticObjectId): The answer to switch to.
        limit(int): The maximum number of messages to return.

    Returns:
        tuple[list[ChatMessageDocument], dict[PydanticObjectId, VariantPosition],
            PydanticObjectId | None]: The newest page of the conversation as it
            now reads, the pager metadata of those answers that have siblings,
            and the cursor to pass as `before` to fetch the older page (`None`
            when the history is exhausted).

    Raises:
        HTTPException: 404 if the ID doesn't identify an answer in this thread.
    """
    by_parent = await _load_thread_nodes(thread.id)  # type: ignore[arg-type]

    target = next(
        (
            node
            for group in by_parent.values()
            for node in group
            if node.id == message_id and node.role == "ai"
        ),
        None,
    )
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found.",
        )

    siblings = by_parent[target.parent_checkpoint_id]
    # The question comes first in its group: it was stored before any of the
    # answers to it, including ones generated much later.
    lower_bound = siblings[0].id

    keep = [node.id for node in siblings if node.role == "human"]
    keep.append(target.id)

    # Walk down the chosen answer's own follow-ups to the end of the branch.
    cursor = target.checkpoint_id
    while cursor is not None:
        children = by_parent.get(cursor, [])
        answers = [child for child in children if child.role == "ai"]
        if not answers:
            break
        chosen = next(
            (child for child in reversed(answers) if child.active), answers[-1]
        )
        keep.extend(child.id for child in children if child.role == "human")
        keep.append(chosen.id)
        cursor = chosen.checkpoint_id

    # The two ranges overlap, so the order matters.
    await ChatMessageDocument.find(
        ChatMessageDocument.thread_id == thread.id,
        {"_id": {"$gte": lower_bound}},
    ).update({"$set": {"active": False}})
    await ChatMessageDocument.find(
        ChatMessageDocument.thread_id == thread.id,
        {"_id": {"$in": keep}},
    ).update({"$set": {"active": True}})

    return await get_thread_messages(thread, limit)


async def stream_agent_response(
    message: str,
    thread_id: str,
    repo_id: str,
    user: User,
    fork: ForkPoint | None = None,
    resume_checkpoint_id: str | None = None,
) -> AsyncIterable[ServerSentEvent]:
    """Stream agent response tokens as SSE-formatted strings.

    This generator yields token, tool, and error events, plus the
    `message_saved` event once the turn is stored. The caller is
    responsible for emitting lifecycle events (`chat_id`, `title`,
    `done`). After the stream completes, both the user message and
    the full AI response are persisted to `ChatMessageDocument` with
    LangGraph checkpoint IDs for history tracking and branch support.

    Passing a `fork` regenerates an existing answer: the graph re-enters at
    the given checkpoint so the model sees the same context it saw the first
    time, and the resulting answer is stored alongside the original rather
    than replacing it.

    Args:
        message(str): The user's chat message.
        thread_id(str): The LangGraph thread ID for conversation persistence.
        repo_id(str): The repository ID for scoping search results.
        user(User): The authenticated user, used to populate the chat context.
        fork(ForkPoint | None): Where to re-enter the graph when regenerating
            an answer. `None` for an ordinary turn.
        resume_checkpoint_id(str | None): The checkpoint to continue the
            conversation from, when the selected branch is not the one the
            graph most recently wrote to.

    Yields:
        ServerSentEvent: SSE-formatted events (token, tool, message_saved, error).
    """
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id, "repo_id": repo_id},
        "recursion_limit": chat_settings.AGENT_RECURSION_LIMIT,
    }
    if resume_checkpoint_id is not None:
        config = _copy_config_with_checkpoint_id(config, resume_checkpoint_id)

    agent = get_agent()
    response: str = ""
    response_sources: list[dict[str, str]] = []
    checkpoint_id: str | None = None
    input_checkpoint_id: str | None = fork.input_checkpoint_id if fork else None

    try:
        safe_config, parent_checkpoint_id, stream_input = await _resolve_entry_point(
            agent, config, message, fork
        )

        async for step in agent.astream(  # type: ignore[call-overload]
            stream_input,
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
                if data["config"]["configurable"]["checkpoint_ns"] != "":
                    continue
                if not data.get("next"):
                    checkpoint_id = data["config"]["configurable"]["checkpoint_id"]
                elif (
                    input_checkpoint_id is None
                    and data.get("metadata", {}).get("source") == "input"
                ):
                    # Recorded now because it cannot be found again later:
                    # the checkpointer's history is scoped to the thread, not
                    # to a branch, so once this turn has siblings a search
                    # through it can surface another branch's checkpoint.
                    input_checkpoint_id = data["config"]["configurable"][
                        "checkpoint_id"
                    ]

    except Exception:
        logger.exception("Chat: Chat stream failed.")
        yield sse_error("An error occurred while generating the response.")
        return

    try:
        human, ai = await _persist_turn(
            thread_id=thread_id,
            message=message,
            response=response,
            checkpoint_id=checkpoint_id,
            parent_checkpoint_id=parent_checkpoint_id,
            sources=_deduplicate_sources(response_sources) or None,
            input_checkpoint_id=input_checkpoint_id,
            persist_human=fork is None,
        )
    except Exception:
        logger.exception("Chat: Failed to persist chat messages.")
        yield sse_error("Failed to save the conversation.")
        return

    try:
        yield await _publish_turn(thread_id, human=human, ai=ai, fork=fork)
    except Exception:
        logger.exception("Chat: Failed to switch to the regenerated answer.")
        yield sse_error("Failed to save the conversation.")
        return


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
