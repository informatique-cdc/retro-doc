"""Unit tests for chat service.

This module tests the chat service against a mongomock database,
with mocks used only where external dependencies or specific call
verification are needed.
"""

from collections.abc import AsyncGenerator, Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.auth.schemas import User
from app.chat.config import chat_settings
from app.chat.models import ChatMessageDocument, ChatThreadDocument
from app.chat.schemas import ChatMessageResponse
from app.chat.service import (
    _copy_config_with_checkpoint_id,
    _deduplicate_sources,
    _persist_turn,
    _prepare_safe_stream,
    _truncate_title,
    create_chat_stream,
    create_thread,
    delete_thread,
    delete_threads_by_repo,
    generate_title,
    get_thread_messages,
    get_user_threads,
    resume_chat_stream,
    stream_agent_response,
    update_thread_timestamp,
    update_thread_title,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _async_iter_error(exc: Exception) -> Callable[..., AsyncGenerator[None, None]]:
    """Return a callable that produces an async iterator that raises."""

    async def _factory(*args: Any, **kwargs: Any) -> AsyncGenerator[None, None]:
        raise exc
        yield

    return _factory


async def _collect_stream_events(
    message: str, tid: str, rid: str, user: User
) -> list[Any]:
    """Collect all SSE events from `stream_agent_response` into a list."""
    return [event async for event in stream_agent_response(message, tid, rid, user)]


async def _make_async_gen(items: list[Any]) -> AsyncGenerator[Any, None]:
    """An async generator that yields items."""
    for item in items:
        yield item


def _make_prepare_stream_mocks(
    state_next: tuple[str, ...],
    checkpoint_id: str | None = None,
    metadata_source: str = "loop",
) -> tuple[AsyncMock, RunnableConfig]:
    """Build the common mock_agent + config pair for `_prepare_safe_stream` tests."""
    mock_agent = AsyncMock()
    mock_state = MagicMock()
    mock_state.next = state_next
    mock_state.metadata = {"source": metadata_source}
    configurable: dict[str, str] = {}
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    mock_state.config = {"configurable": configurable}
    mock_agent.aget_state.return_value = mock_state
    config: RunnableConfig = {"configurable": {"thread_id": "tid", "repo_id": "rid"}}
    return mock_agent, config


# ---------------------------------------------------------------------------
# _copy_config_with_checkpoint_id
# ---------------------------------------------------------------------------


def test_copy_config_with_checkpoint_id_sets_id() -> None:
    """Adds checkpoint_id alongside existing configurable keys."""
    config: RunnableConfig = {"configurable": {"thread_id": "tid", "repo_id": "rid"}}

    result = _copy_config_with_checkpoint_id(config, "ckpt-1")

    assert result["configurable"]["checkpoint_id"] == "ckpt-1"
    assert result["configurable"]["thread_id"] == "tid"
    assert result["configurable"]["repo_id"] == "rid"


def test_copy_config_with_checkpoint_id_overrides_existing() -> None:
    """Replaces an existing checkpoint_id value."""
    config: RunnableConfig = {
        "configurable": {"thread_id": "tid", "checkpoint_id": "old-ckpt"}
    }

    result = _copy_config_with_checkpoint_id(config, "new-ckpt")

    assert result["configurable"]["checkpoint_id"] == "new-ckpt"


def test_copy_config_with_checkpoint_id_handles_missing_configurable() -> None:
    """Creates the configurable section when absent."""
    config: RunnableConfig = {}

    result = _copy_config_with_checkpoint_id(config, "ckpt-1")

    assert result["configurable"]["checkpoint_id"] == "ckpt-1"


def test_copy_config_with_checkpoint_id_does_not_mutate_original() -> None:
    """Original config dict is not modified."""
    config: RunnableConfig = {"configurable": {"thread_id": "tid"}}

    _copy_config_with_checkpoint_id(config, "ckpt-1")

    assert "checkpoint_id" not in config["configurable"]


# ---------------------------------------------------------------------------
# _deduplicate_sources
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sources", "expected"),
    [
        (
            [
                {"path": "src/main.py", "file_id": "aaa"},
                {"path": "src/utils.py", "file_id": "bbb"},
            ],
            [
                {"path": "src/main.py", "file_id": "aaa"},
                {"path": "src/utils.py", "file_id": "bbb"},
            ],
        ),
        (
            [
                {"path": "src/main.py", "file_id": "aaa"},
                {"path": "src/utils.py", "file_id": "bbb"},
                {"path": "src/main.py", "file_id": "ccc"},
            ],
            [
                {"path": "src/main.py", "file_id": "aaa"},
                {"path": "src/utils.py", "file_id": "bbb"},
            ],
        ),
    ],
    ids=["no_duplicates", "with_duplicates"],
)
def test_deduplicate_sources(
    sources: list[dict[str, str]], expected: list[dict[str, str]]
) -> None:
    """Removes duplicate sources by path, keeping first occurrence."""
    assert _deduplicate_sources(sources) == expected


# ---------------------------------------------------------------------------
# _persist_turn
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "parent_checkpoint_id",
    ["ckpt-0", None],
    ids=["with_parent", "none_parent"],
)
async def test_persist_turn_inserts_two_messages(
    parent_checkpoint_id: str | None,
) -> None:
    """Inserts both human and AI messages with the correct checkpoint IDs."""
    mock_msg_instance = MagicMock()
    mock_msg_instance.insert = AsyncMock()

    with patch(
        "app.chat.service.ChatMessageDocument", return_value=mock_msg_instance
    ) as mock_cls:
        await _persist_turn(
            thread_id="000000000000000000000010",
            message="Hi",
            response="Hello!",
            checkpoint_id="ckpt-1",
            parent_checkpoint_id=parent_checkpoint_id,
        )

    assert mock_cls.call_count == 2
    assert mock_msg_instance.insert.await_count == 2

    calls = mock_cls.call_args_list
    assert calls[0].kwargs["role"] == "human"
    assert calls[0].kwargs["content"] == "Hi"
    assert calls[0].kwargs["checkpoint_id"] == "ckpt-1"
    assert calls[0].kwargs["parent_checkpoint_id"] == parent_checkpoint_id
    assert calls[1].kwargs["role"] == "ai"
    assert calls[1].kwargs["content"] == "Hello!"
    assert calls[1].kwargs["checkpoint_id"] == "ckpt-1"
    assert calls[1].kwargs["parent_checkpoint_id"] == parent_checkpoint_id


# ---------------------------------------------------------------------------
# _prepare_safe_stream
# ---------------------------------------------------------------------------


async def test_prepare_safe_stream_walks_back_when_stale() -> None:
    """Searches history for input checkpoint and walks back to oldest input."""
    mock_agent, config = _make_prepare_stream_mocks(
        state_next=("tools",), checkpoint_id="stale-ckpt", metadata_source="loop"
    )

    # History returns a single input checkpoint.
    input_snap = MagicMock()
    input_snap.config = {"configurable": {"checkpoint_id": "input-ckpt"}}
    input_snap.metadata = {"source": "input"}
    input_snap.parent_config = {"configurable": {"checkpoint_id": "parent-ckpt"}}

    mock_agent.aget_state_history = MagicMock(
        return_value=_make_async_gen([input_snap])
    )

    # Walking back from input_snap: parent is not an input checkpoint.
    parent_state = MagicMock()
    parent_state.config = {"configurable": {"checkpoint_id": "parent-ckpt"}}
    parent_state.metadata = {"source": "loop"}
    parent_state.parent_config = {"configurable": {"checkpoint_id": "root-ckpt"}}

    mock_agent.aget_state.side_effect = [
        mock_agent.aget_state.return_value,  # initial call
        parent_state,  # walk-back call
    ]

    result_config, parent_id = await _prepare_safe_stream(mock_agent, config)

    assert result_config["configurable"]["checkpoint_id"] == "input-ckpt"
    assert result_config["configurable"]["repo_id"] == "rid"
    assert parent_id == "parent-ckpt"


@pytest.mark.parametrize(
    ("checkpoint_id", "expected_parent"),
    [
        ("prev-ckpt", "prev-ckpt"),
        (None, None),
    ],
    ids=["healthy", "no_checkpoint"],
)
async def test_prepare_safe_stream_passthrough(
    checkpoint_id: str | None,
    expected_parent: str | None,
) -> None:
    """Returns config unchanged when graph is healthy or brand-new."""
    mock_agent, config = _make_prepare_stream_mocks(
        state_next=(), checkpoint_id=checkpoint_id
    )

    result_config, parent_ckpt = await _prepare_safe_stream(mock_agent, config)

    assert parent_ckpt == expected_parent
    if checkpoint_id is not None:
        assert result_config["configurable"]["checkpoint_id"] == checkpoint_id
    else:
        assert "checkpoint_id" not in result_config["configurable"]


async def test_prepare_safe_stream_raises_when_no_input_checkpoint() -> None:
    """Raises `RuntimeError` when stale but no input checkpoint in history."""
    mock_agent, config = _make_prepare_stream_mocks(
        state_next=("tools",), checkpoint_id="stale-ckpt", metadata_source="loop"
    )

    mock_agent.aget_state_history = MagicMock(return_value=_make_async_gen([]))

    with pytest.raises(RuntimeError, match="no checkpoint with source `input`"):
        await _prepare_safe_stream(mock_agent, config)


# ---------------------------------------------------------------------------
# _truncate_title
# ---------------------------------------------------------------------------


def test_truncate_title_long_message() -> None:
    """Long message truncated on word boundary with `...`."""
    long_msg = "word " * chat_settings.TITLE_MAX_LEN
    result = _truncate_title(long_msg)

    assert result.endswith("...")
    assert len(result) <= chat_settings.TITLE_MAX_LEN


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Hello", "Hello"),
        ("a" * chat_settings.TITLE_MAX_LEN, "a" * chat_settings.TITLE_MAX_LEN),
    ],
    ids=["short", "exactly_at_limit"],
)
def test_truncate_title_within_limit(message: str, expected: str) -> None:
    """Messages at or below the limit are returned unchanged."""
    assert _truncate_title(message) == expected


# ---------------------------------------------------------------------------
# create_thread
# ---------------------------------------------------------------------------


async def test_create_thread_persists(
    user: User,
    repo_id: PydanticObjectId,
) -> None:
    """Creates and persists a `ChatThreadDocument` with a truncated title."""
    message = "How does the authentication module work?"
    thread = await create_thread(repo_id, user, message)

    refreshed = await ChatThreadDocument.get(thread.id)
    assert refreshed is not None
    assert refreshed.user_id == user.uid
    assert refreshed.repo_id == repo_id
    assert refreshed.title == _truncate_title(message)


# ---------------------------------------------------------------------------
# delete_thread
# ---------------------------------------------------------------------------


async def test_delete_thread_removes(
    persisted_thread_doc: ChatThreadDocument,
) -> None:
    """Deletes the ChatThreadDocument from the database."""
    thread_id = persisted_thread_doc.id

    await delete_thread(persisted_thread_doc)

    assert await ChatThreadDocument.get(thread_id) is None


# ---------------------------------------------------------------------------
# delete_threads_by_repo
# ---------------------------------------------------------------------------


async def test_delete_threads_by_repo_no_threads(
    user: User,
    repo_id: PydanticObjectId,
) -> None:
    """Does nothing when no threads exist for the user and repo."""
    await delete_threads_by_repo(user.uid, repo_id)  # type: ignore[arg-type]


async def test_delete_threads_by_repo_removes_all(
    user: User,
    repo_id: PydanticObjectId,
    persisted_thread_doc: ChatThreadDocument,
) -> None:
    """Deletes all threads for the user+repo combination."""
    await delete_threads_by_repo(user.uid, repo_id)  # type: ignore[arg-type]

    threads = await ChatThreadDocument.find(
        ChatThreadDocument.user_id == user.uid,
        ChatThreadDocument.repo_id == repo_id,
    ).to_list()
    assert threads == []


# ---------------------------------------------------------------------------
# generate_title
# ---------------------------------------------------------------------------


async def test_generate_title_fallback_on_llm_failure(
    mock_thread_doc: MagicMock,
) -> None:
    """Falls back to the existing thread title when LLM fails, without saving."""
    message = "Short msg"

    with patch("app.chat.service.title_model") as mock_model:
        mock_model.ainvoke = AsyncMock(side_effect=RuntimeError("LLM down"))
        result = await generate_title(mock_thread_doc, message)

    assert result == mock_thread_doc.title
    mock_thread_doc.save.assert_not_awaited()


async def test_generate_title_persists(
    persisted_thread_doc: ChatThreadDocument,
) -> None:
    """Generated title is persisted to the database."""
    mock_response = MagicMock()
    mock_response.text = "My Title"

    with patch("app.chat.service.title_model") as mock_model:
        mock_model.ainvoke = AsyncMock(return_value=mock_response)
        result = await generate_title(persisted_thread_doc, "What is this?")

    assert result == "My Title"

    refreshed = await ChatThreadDocument.get(persisted_thread_doc.id)
    assert refreshed is not None
    assert refreshed.title == "My Title"


async def test_generate_title_truncates_long_title(
    mock_thread_doc: MagicMock,
    fake_chat_model: Callable[..., GenericFakeChatModel],
) -> None:
    """Title from LLM is truncated if too long."""
    long_title = "word " * chat_settings.TITLE_MAX_LEN
    model = fake_chat_model([long_title])

    with patch("app.chat.service.title_model", model):
        result = await generate_title(mock_thread_doc, "msg")

    assert len(result) <= chat_settings.TITLE_MAX_LEN


# ---------------------------------------------------------------------------
# get_user_threads
# ---------------------------------------------------------------------------


async def test_get_user_threads_empty_db(user: User) -> None:
    """Returns an empty list when no threads exist."""
    result = await get_user_threads(user)

    assert result == []


async def test_get_user_threads_filtered_by_repo_id(
    user: User,
    repo_id: PydanticObjectId,
    persisted_thread_doc: ChatThreadDocument,
) -> None:
    """Returns threads filtered by `repo_id`."""
    result = await get_user_threads(user, repo_id=repo_id)

    assert len(result) == 1

    other_repo_id = PydanticObjectId("000000000000000000000099")
    result = await get_user_threads(user, repo_id=other_repo_id)

    assert result == []


async def test_get_user_threads_filtered_by_search(
    user: User,
    persisted_thread_doc: ChatThreadDocument,
) -> None:
    """Returns threads matching search term in title."""
    persisted_thread_doc.title = "Architecture Overview"
    await persisted_thread_doc.save()

    result = await get_user_threads(user, search="archit")

    assert len(result) == 1

    result = await get_user_threads(user, search="nonexistent")

    assert result == []


async def test_get_user_threads_returns_threads(
    user: User,
    persisted_thread_doc: ChatThreadDocument,
) -> None:
    """Returns all threads for the user."""
    result = await get_user_threads(user)

    assert len(result) == 1
    assert result[0].id == persisted_thread_doc.id


async def test_get_user_threads_sorted_by_updated_at(
    user: User,
    repo_id: PydanticObjectId,
) -> None:
    """Threads are sorted by `updated_at` descending."""
    thread1 = await create_thread(repo_id, user, "First message")
    thread2 = await create_thread(repo_id, user, "Second message")

    # Set distinct timestamps to avoid same-millisecond flakiness
    thread1.updated_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    await thread1.save()
    thread2.updated_at = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(hours=1)
    await thread2.save()

    result = await get_user_threads(user)

    assert len(result) == 2
    assert result[0].id == thread2.id
    assert result[1].id == thread1.id


# ---------------------------------------------------------------------------
# get_thread_messages
# ---------------------------------------------------------------------------


async def test_get_thread_messages_chronological(
    persisted_thread_doc: ChatThreadDocument,
    persisted_message_docs: list[ChatMessageDocument],
) -> None:
    """Returns messages in chronological order."""
    result = await get_thread_messages(persisted_thread_doc)

    assert len(result) == 2
    assert isinstance(result[0], ChatMessageResponse)
    assert result[0].role == "human"
    assert result[0].content == "Hello"
    assert result[1].role == "ai"
    assert result[1].content == "Hi there!"


async def test_get_thread_messages_empty(
    persisted_thread_doc: ChatThreadDocument,
) -> None:
    """Returns an empty list when no messages exist."""
    result = await get_thread_messages(persisted_thread_doc)

    assert result == []


# ---------------------------------------------------------------------------
# update_thread_title
# ---------------------------------------------------------------------------


async def test_update_thread_title_persists(
    persisted_thread_doc: ChatThreadDocument,
) -> None:
    """Updated title is persisted to the database."""
    await update_thread_title(persisted_thread_doc, "New Title")

    refreshed = await ChatThreadDocument.get(persisted_thread_doc.id)
    assert refreshed is not None
    assert refreshed.title == "New Title"


# ---------------------------------------------------------------------------
# update_thread_timestamp
# ---------------------------------------------------------------------------


async def test_update_thread_timestamp_persists(
    persisted_thread_doc: ChatThreadDocument,
) -> None:
    """Updated timestamp is persisted to the database."""

    # Set a known old timestamp to avoid microsecond-precision issues with mongomock
    persisted_thread_doc.updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    await persisted_thread_doc.save()

    await update_thread_timestamp(persisted_thread_doc)

    refreshed = await ChatThreadDocument.get(persisted_thread_doc.id)
    assert refreshed is not None
    assert refreshed.updated_at > datetime(2020, 1, 1)


# ---------------------------------------------------------------------------
# stream_agent_response
# ---------------------------------------------------------------------------


async def test_stream_agent_response_yields_error_on_exception(user: User) -> None:
    """Yields an error event when the agent stream fails."""
    mock_agent = AsyncMock()
    mock_agent.astream = _async_iter_error(RuntimeError("boom"))

    config: RunnableConfig = {"configurable": {"thread_id": "tid", "repo_id": "rid"}}

    with (
        patch("app.chat.service.get_agent", return_value=mock_agent),
        patch(
            "app.chat.service._prepare_safe_stream",
            new_callable=AsyncMock,
            return_value=(config, "prev-ckpt"),
        ),
    ):
        results = await _collect_stream_events("Hi", "tid", "rid", user)

    assert len(results) == 1
    assert results[0].event == "error"


async def test_stream_agent_response_yields_error_on_persist_failure(
    user: User,
    fake_agent: Callable[..., CompiledStateGraph],  # type: ignore[type-arg]
) -> None:
    """Yields an error event when persisting messages fails."""
    agent = fake_agent(["Hello"])

    with (
        patch("app.chat.service.get_agent", return_value=agent),
        patch(
            "app.chat.service._persist_turn",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB down"),
        ),
    ):
        results = await _collect_stream_events("Hi", "tid", "rid", user)

    error_events = [e for e in results if e.event == "error"]
    assert len(error_events) == 1


async def test_stream_agent_response_yields_token_events(
    user: User,
    fake_agent: Callable[..., CompiledStateGraph],  # type: ignore[type-arg]
) -> None:
    """Yields token SSE events from agent stream."""
    agent = fake_agent(["Hello world"])

    with (
        patch("app.chat.service.get_agent", return_value=agent),
        patch("app.chat.service._persist_turn", new_callable=AsyncMock),
    ):
        results = await _collect_stream_events("Hi", "tid", "rid", user)

    token_events = [e for e in results if e.data and "token" in e.data]
    assert len(token_events) > 0

    full_response = "".join(e.data["token"] for e in token_events)
    assert "Hello world" in full_response


async def test_stream_agent_response_uses_stream_config(
    user: User,
    fake_agent: Callable[..., CompiledStateGraph],  # type: ignore[type-arg]
) -> None:
    """Passes the resolved config and message to astream."""
    agent = fake_agent(["Hello"])
    resolved_config: RunnableConfig = {
        "configurable": {
            "thread_id": "tid",
            "repo_id": "rid",
            "checkpoint_id": "pinned-ckpt",
        }
    }

    with (
        patch("app.chat.service.get_agent", return_value=agent),
        patch("app.chat.service._persist_turn", new_callable=AsyncMock),
        patch(
            "app.chat.service._prepare_safe_stream",
            new_callable=AsyncMock,
            return_value=(resolved_config, "prev-ckpt"),
        ),
        patch.object(agent, "astream", wraps=agent.astream) as spy,
    ):
        await _collect_stream_events("Hi", "tid", "rid", user)

    call_args, call_kwargs = spy.call_args
    assert call_args[0] == {"messages": [{"role": "user", "content": "Hi"}]}
    assert call_kwargs["config"] is resolved_config


async def test_stream_agent_response_yields_error_on_resolve_failure(
    user: User,
) -> None:
    """Yields an error event when _prepare_safe_stream raises."""
    mock_agent = AsyncMock()

    with (
        patch("app.chat.service.get_agent", return_value=mock_agent),
        patch(
            "app.chat.service._prepare_safe_stream",
            new_callable=AsyncMock,
            side_effect=RuntimeError("recovery failed"),
        ),
    ):
        results = await _collect_stream_events("Hi", "tid", "rid", user)

    assert len(results) == 1
    assert results[0].event == "error"


# ---------------------------------------------------------------------------
# create_chat_stream
# ---------------------------------------------------------------------------


async def test_create_chat_stream_success(
    user: User,
    repo_id: PydanticObjectId,
    mock_thread_doc: MagicMock,
    mock_token_event: MagicMock,
) -> None:
    """Yields `chat_id`, then tokens, then `title`, then `done`."""
    with (
        patch(
            "app.chat.service.create_thread",
            new_callable=AsyncMock,
            return_value=mock_thread_doc,
        ),
        patch(
            "app.chat.service.generate_title",
            new_callable=AsyncMock,
            return_value="My Title",
        ),
        patch("app.chat.service.stream_agent_response") as mock_stream,
    ):
        mock_stream.return_value = _make_async_gen([mock_token_event])
        results = [event async for event in create_chat_stream(repo_id, "Hello", user)]

    assert results[0].event == "chat_id"
    assert results[-2].event == "title"
    assert results[-1].event == "done"


async def test_create_chat_stream_thread_creation_failure(
    user: User,
    repo_id: PydanticObjectId,
) -> None:
    """Yields `error` and `done` when thread creation fails."""
    with patch(
        "app.chat.service.create_thread",
        new_callable=AsyncMock,
        side_effect=RuntimeError("DB error"),
    ):
        results = [event async for event in create_chat_stream(repo_id, "Hello", user)]

    assert len(results) == 2
    assert results[0].event == "error"
    assert results[1].event == "done"


# ---------------------------------------------------------------------------
# resume_chat_stream
# ---------------------------------------------------------------------------


async def test_resume_chat_stream_yields_events_and_done(
    user: User,
    mock_thread_doc: MagicMock,
    mock_token_event: MagicMock,
) -> None:
    """Updates timestamp, yields events, then `done`."""
    with (
        patch("app.chat.service.update_thread_timestamp", new_callable=AsyncMock),
        patch("app.chat.service.stream_agent_response") as mock_stream,
    ):
        mock_stream.return_value = _make_async_gen([mock_token_event])
        results = [
            event async for event in resume_chat_stream(mock_thread_doc, "Hello", user)
        ]

    assert results[-1].event == "done"
