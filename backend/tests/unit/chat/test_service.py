"""Unit tests for chat service.

This module tests the chat service against a mongomock database,
with mocks used only where external dependencies or specific call
verification are needed.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TypedDict
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException
from fastapi.sse import ServerSentEvent
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from app.auth.schemas import User
from app.chat import service as chat_service
from app.chat.config import chat_settings
from app.chat.models import ChatMessageDocument, ChatMessageView, ChatThreadDocument
from app.chat.schemas import ChatContext
from app.chat.service import (
    ForkPoint,
    _copy_config_with_checkpoint_id,
    _deduplicate_sources,
    _persist_turn,
    _prepare_safe_stream,
    _truncate_title,
    _variant_positions,
    create_chat_stream,
    create_thread,
    delete_thread,
    delete_threads_by_repo,
    generate_title,
    get_thread_messages,
    get_user_threads,
    resume_chat_stream,
    retry_message_stream,
    select_variant,
    stream_agent_response,
    update_thread_timestamp,
    update_thread_title,
)


class _ThreadFilters(TypedDict, total=False):
    """The optional filters `get_user_threads` accepts."""

    repo_id: PydanticObjectId
    search: str


_STREAM_CONFIG: RunnableConfig = {
    "configurable": {"thread_id": "tid", "repo_id": "rid"}
}


def _async_iter_error(exc: Exception) -> Callable[..., AsyncGenerator[None, None]]:
    """Return a callable that produces an async iterator that raises."""

    async def _factory(*args: object, **kwargs: object) -> AsyncGenerator[None, None]:
        raise exc
        yield

    return _factory


async def _collect_fork_events(
    thread_id: str, fork: ForkPoint, user: User, message: str = "Q1"
) -> list[ServerSentEvent]:
    """Collect all SSE events from a regenerating `stream_agent_response`."""
    return [
        event
        async for event in stream_agent_response(
            message, thread_id, "rid", user, fork=fork
        )
    ]


async def _collect_stream_events(
    message: str, thread_id: str, repo_id: str, user: User
) -> list[ServerSentEvent]:
    """Collect all SSE events from a fresh `stream_agent_response` turn."""
    return [
        event
        async for event in stream_agent_response(message, thread_id, repo_id, user)
    ]


async def _make_async_gen(items: list[MagicMock]) -> AsyncGenerator[MagicMock, None]:
    """An async generator that yields items."""
    for item in items:
        yield item


def _node(oid: str) -> ChatMessageView:
    """A minimal projected node, for the pure numbering helper."""
    return ChatMessageView.model_validate({"_id": PydanticObjectId(oid), "role": "ai"})


@pytest.fixture
def chat_id() -> PydanticObjectId:
    """A fixed chat thread ID for tests."""
    return PydanticObjectId("000000000000000000000010")


@pytest.fixture
def collect_stream_events(
    user: User,
) -> Callable[[], Awaitable[list[ServerSentEvent]]]:
    """Factory collecting every SSE event from `stream_agent_response`."""

    async def _factory() -> list[ServerSentEvent]:
        return [
            event async for event in stream_agent_response("Hi", "tid", "rid", user)
        ]

    return _factory


@pytest.fixture
def fake_agent() -> Callable[..., CompiledStateGraph]:  # type: ignore[type-arg]
    """Factory that builds a LangGraph agent backed by `GenericFakeChatModel`."""

    def _factory(
        responses: list[str | AIMessage],
        tools: list | None = None,  # type: ignore[type-arg]
    ) -> CompiledStateGraph:  # type: ignore[type-arg]
        model = GenericFakeChatModel(messages=iter(responses))
        return create_agent(
            model,
            tools=tools or [],
            context_schema=ChatContext,  # type: ignore[arg-type]
            checkpointer=InMemorySaver(),
        )

    return _factory


@pytest.fixture
def fake_chat_model() -> Callable[..., GenericFakeChatModel]:
    """Factory that builds a `GenericFakeChatModel` from scripted responses."""

    def _factory(responses: list[str | AIMessage]) -> GenericFakeChatModel:
        return GenericFakeChatModel(messages=iter(responses))

    return _factory


@pytest.fixture
def mock_thread_doc(
    chat_id: PydanticObjectId, user: User, repo_id: PydanticObjectId
) -> MagicMock:
    """A mocked `ChatThreadDocument`."""
    thread = MagicMock(spec=ChatThreadDocument)
    thread.id = chat_id
    thread.user_id = user.uid
    thread.repo_id = repo_id
    thread.title = "Test thread"
    thread.has_variants = False
    thread.created_at = datetime(2025, 1, 1, tzinfo=UTC)
    thread.updated_at = datetime(2025, 1, 1, tzinfo=UTC)
    thread.insert = AsyncMock()
    thread.save = AsyncMock()
    thread.delete = AsyncMock()
    return thread


@pytest.fixture
def mock_token_event() -> MagicMock:
    """A mock SSE token event for stream tests."""
    event = MagicMock()
    event.data = {"token": "Hi"}
    event.event = None
    return event


@pytest.fixture
async def persisted_message_docs(
    persisted_thread_doc: ChatThreadDocument,
) -> list[ChatMessageDocument]:
    """A list of `ChatMessageDocument` instances persisted in mongomock."""
    human = ChatMessageDocument(
        thread_id=persisted_thread_doc.id,
        checkpoint_id="ckpt-1",
        parent_checkpoint_id="ckpt-0",
        role="human",
        content="Hello",
    )
    ai = ChatMessageDocument(
        thread_id=persisted_thread_doc.id,
        checkpoint_id="ckpt-1",
        parent_checkpoint_id="ckpt-0",
        role="ai",
        content="Hi there!",
    )
    await human.insert()
    await ai.insert()
    return [human, ai]


@pytest.fixture
def prepare_stream_mocks() -> Callable[..., tuple[AsyncMock, RunnableConfig]]:
    """Factory building the agent and config pair for `_prepare_safe_stream` tests."""

    def _factory(
        state_next: tuple[str, ...],
        checkpoint_id: str | None = None,
        metadata_source: str = "loop",
    ) -> tuple[AsyncMock, RunnableConfig]:
        mock_agent = AsyncMock()
        mock_state = MagicMock()
        mock_state.next = state_next
        mock_state.metadata = {"source": metadata_source}
        configurable: dict[str, str] = {}
        if checkpoint_id is not None:
            configurable["checkpoint_id"] = checkpoint_id
        mock_state.config = {"configurable": configurable}
        mock_agent.aget_state.return_value = mock_state
        config: RunnableConfig = {
            "configurable": {"thread_id": "tid", "repo_id": "rid"}
        }
        return mock_agent, config

    return _factory


class TestCopyConfigWithCheckpointId:
    """Copy a runnable config with a checkpoint ID pinned onto it."""

    @pytest.mark.parametrize(
        ("config", "checkpoint_id", "expected"),
        [
            pytest.param(
                {"configurable": {"thread_id": "tid", "repo_id": "rid"}},
                "ckpt-1",
                {"thread_id": "tid", "repo_id": "rid", "checkpoint_id": "ckpt-1"},
                id="sets-id-and-preserves-siblings",
            ),
            pytest.param(
                {"configurable": {"thread_id": "tid", "checkpoint_id": "old-ckpt"}},
                "new-ckpt",
                {"thread_id": "tid", "checkpoint_id": "new-ckpt"},
                id="overrides-existing",
            ),
            pytest.param(
                {},
                "ckpt-1",
                {"checkpoint_id": "ckpt-1"},
                id="missing-configurable",
            ),
        ],
    )
    def test_copy_config_with_checkpoint_id(
        self, config: RunnableConfig, checkpoint_id: str, expected: dict[str, str]
    ) -> None:
        """Sets `checkpoint_id` without disturbing the other configurable keys."""
        result = _copy_config_with_checkpoint_id(config, checkpoint_id)

        assert result["configurable"] == expected

    def test_copy_config_with_checkpoint_id_does_not_mutate_original(self) -> None:
        """Original config dict is not modified."""
        config: RunnableConfig = {"configurable": {"thread_id": "tid"}}

        _copy_config_with_checkpoint_id(config, "ckpt-1")

        assert "checkpoint_id" not in config["configurable"]


class TestCreateChatStream:
    """Open a new thread and stream its first turn back to the client."""

    async def test_create_chat_stream_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        repo_id: PydanticObjectId,
        mock_thread_doc: MagicMock,
        mock_token_event: MagicMock,
    ) -> None:
        """Yields `chat_id`, then tokens, then `title`, then `done`."""
        monkeypatch.setattr(
            chat_service, "create_thread", AsyncMock(return_value=mock_thread_doc)
        )
        monkeypatch.setattr(
            chat_service, "generate_title", AsyncMock(return_value="My Title")
        )
        monkeypatch.setattr(
            chat_service,
            "stream_agent_response",
            MagicMock(return_value=_make_async_gen([mock_token_event])),
        )

        results = [event async for event in create_chat_stream(repo_id, "Hello", user)]

        assert results[0].event == "chat_id"
        assert results[-2].event == "title"
        assert results[-1].event == "done"

    async def test_create_chat_stream_thread_creation_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        repo_id: PydanticObjectId,
    ) -> None:
        """Yields `error` and `done` when thread creation fails."""
        monkeypatch.setattr(
            chat_service,
            "create_thread",
            AsyncMock(side_effect=RuntimeError("DB error")),
        )

        results = [event async for event in create_chat_stream(repo_id, "Hello", user)]

        assert len(results) == 2
        assert results[0].event == "error"
        assert results[1].event == "done"


class TestCreateThread:
    """Persist a new chat thread for a repository."""

    async def test_create_thread_persists(
        self,
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


class TestDeduplicateSources:
    """Collapse the sources cited by a turn to one entry per path."""

    @pytest.mark.parametrize(
        ("sources", "expected"),
        [
            pytest.param(
                [
                    {"path": "src/main.py", "file_id": "aaa"},
                    {"path": "src/utils.py", "file_id": "bbb"},
                ],
                [
                    {"path": "src/main.py", "file_id": "aaa"},
                    {"path": "src/utils.py", "file_id": "bbb"},
                ],
                id="no-duplicates",
            ),
            pytest.param(
                [
                    {"path": "src/main.py", "file_id": "aaa"},
                    {"path": "src/utils.py", "file_id": "bbb"},
                    {"path": "src/main.py", "file_id": "ccc"},
                ],
                [
                    {"path": "src/main.py", "file_id": "aaa"},
                    {"path": "src/utils.py", "file_id": "bbb"},
                ],
                id="with-duplicates",
            ),
        ],
    )
    def test_deduplicate_sources(
        self, sources: list[dict[str, str]], expected: list[dict[str, str]]
    ) -> None:
        """Removes duplicate sources by path, keeping first occurrence."""
        assert _deduplicate_sources(sources) == expected


class TestDeleteThread:
    """Remove a chat thread from the database."""

    async def test_delete_thread_removes(
        self,
        persisted_thread_doc: ChatThreadDocument,
    ) -> None:
        """Deletes the ChatThreadDocument from the database."""
        thread_id = persisted_thread_doc.id

        await delete_thread(persisted_thread_doc)

        assert await ChatThreadDocument.get(thread_id) is None


class TestDeleteThreadsByRepo:
    """Remove every chat thread a user holds for one repository."""

    @pytest.mark.parametrize(
        "seeded",
        [
            pytest.param(0, id="no-threads"),
            pytest.param(1, id="single-thread"),
            pytest.param(3, id="several-threads"),
        ],
    )
    async def test_delete_threads_by_repo(
        self,
        user: User,
        repo_id: PydanticObjectId,
        seeded: int,
    ) -> None:
        """Removes every thread of the user and repo, whatever the count."""
        for index in range(seeded):
            await ChatThreadDocument(
                user_id=user.uid,
                repo_id=repo_id,
                title=f"Thread {index}",
            ).insert()

        await delete_threads_by_repo(user.uid, repo_id)  # type: ignore[arg-type]

        threads = await ChatThreadDocument.find(
            ChatThreadDocument.user_id == user.uid,
            ChatThreadDocument.repo_id == repo_id,
        ).to_list()
        assert threads == []


class TestGenerateTitle:
    """Name a thread from its first message, falling back to its current title."""

    async def test_generate_title_fallback_on_llm_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_thread_doc: MagicMock,
    ) -> None:
        """Falls back to the existing thread title when LLM fails, without saving."""
        message = "Short msg"
        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(side_effect=RuntimeError("LLM down"))
        monkeypatch.setattr(chat_service, "title_model", mock_model)

        result = await generate_title(mock_thread_doc, message)

        assert result == mock_thread_doc.title
        mock_thread_doc.save.assert_not_awaited()

    async def test_generate_title_persists(
        self,
        monkeypatch: pytest.MonkeyPatch,
        persisted_thread_doc: ChatThreadDocument,
        fake_chat_model: Callable[..., GenericFakeChatModel],
    ) -> None:
        """Generated title is persisted to the database."""
        model = fake_chat_model(["My Title"])
        monkeypatch.setattr(chat_service, "title_model", model)

        result = await generate_title(persisted_thread_doc, "What is this?")

        assert result == "My Title"

        refreshed = await ChatThreadDocument.get(persisted_thread_doc.id)
        assert refreshed is not None
        assert refreshed.title == "My Title"

    async def test_generate_title_truncates_long_title(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_thread_doc: MagicMock,
        fake_chat_model: Callable[..., GenericFakeChatModel],
    ) -> None:
        """Title from LLM is truncated if too long."""
        long_title = "word " * chat_settings.TITLE_MAX_LEN
        model = fake_chat_model([long_title])
        monkeypatch.setattr(chat_service, "title_model", model)

        result = await generate_title(mock_thread_doc, "msg")

        assert result == _truncate_title(long_title.strip())


class TestGetThreadMessages:
    """Page the active messages of a thread, annotating regenerated answers."""

    @pytest.mark.usefixtures("persisted_message_docs")
    async def test_get_thread_messages_chronological(
        self,
        persisted_thread_doc: ChatThreadDocument,
    ) -> None:
        """Returns messages in chronological order."""
        messages, _, next_cursor = await get_thread_messages(
            persisted_thread_doc, limit=10
        )

        assert len(messages) == 2
        assert isinstance(messages[0], ChatMessageDocument)
        assert messages[0].role == "human"
        assert messages[0].content == "Hello"
        assert messages[1].role == "ai"
        assert messages[1].content == "Hi there!"
        assert next_cursor is None

    @pytest.mark.parametrize(
        ("seeded", "foreign_seeded", "limit", "expected_contents", "expects_cursor"),
        [
            pytest.param(0, 0, 10, [], False, id="empty-thread"),
            pytest.param(
                3,
                0,
                3,
                ["Message 0", "Message 1", "Message 2"],
                False,
                id="page-exactly-covers-history",
            ),
            pytest.param(
                10,
                0,
                3,
                ["Message 7", "Message 8", "Message 9"],
                True,
                id="newest-page-only",
            ),
            pytest.param(
                2,
                5,
                10,
                ["Message 0", "Message 1"],
                False,
                id="ignores-other-threads",
            ),
        ],
    )
    async def test_get_thread_messages_page(
        self,
        persisted_thread_doc: ChatThreadDocument,
        persist_messages: Callable[..., Awaitable[list[ChatMessageDocument]]],
        seeded: int,
        foreign_seeded: int,
        limit: int,
        expected_contents: list[str],
        expects_cursor: bool,
    ) -> None:
        """Without a cursor, the most recent `limit` messages are returned."""
        await persist_messages(persisted_thread_doc.id, seeded)
        await persist_messages(PydanticObjectId(), foreign_seeded)

        messages, _, next_cursor = await get_thread_messages(
            persisted_thread_doc, limit=limit
        )

        assert [m.content for m in messages] == expected_contents
        assert next_cursor == (messages[0].id if expects_cursor else None)

    async def test_get_thread_messages_before_returns_older_page(
        self,
        persisted_thread_doc: ChatThreadDocument,
        persist_messages: Callable[..., Awaitable[list[ChatMessageDocument]]],
    ) -> None:
        """`before` returns the strictly older page, with no overlap."""
        await persist_messages(persisted_thread_doc.id, 10)

        first, _, cursor = await get_thread_messages(persisted_thread_doc, limit=3)
        second, _, next_cursor = await get_thread_messages(
            persisted_thread_doc, limit=3, before=cursor
        )

        assert [m.content for m in second] == ["Message 4", "Message 5", "Message 6"]
        assert next_cursor is not None
        assert {m.id for m in second}.isdisjoint({m.id for m in first})

    @pytest.mark.parametrize(
        ("count", "limit", "created_at"),
        [
            pytest.param(10, 3, None, id="distinct-timestamps"),
            pytest.param(
                4,
                2,
                datetime(2025, 1, 1, tzinfo=UTC),
                id="identical-timestamps",
            ),
        ],
    )
    async def test_get_thread_messages_walking_cursors_covers_history(
        self,
        persisted_thread_doc: ChatThreadDocument,
        persist_messages: Callable[..., Awaitable[list[ChatMessageDocument]]],
        count: int,
        limit: int,
        created_at: datetime | None,
    ) -> None:
        """Walking cursors to exhaustion yields every message exactly once.

        Messages sharing a `created_at` are not skipped or repeated either:
        `_persist_turn` writes both messages of a turn back-to-back, so ties are
        routine; the keyset is on `_id`, which cannot tie.
        """
        await persist_messages(persisted_thread_doc.id, count, created_at)

        collected: list[ChatMessageDocument] = []
        cursor: PydanticObjectId | None = None
        while True:
            page, _, cursor = await get_thread_messages(
                persisted_thread_doc, limit=limit, before=cursor
            )
            collected = page + collected
            if cursor is None:
                break

        assert [m.content for m in collected] == [f"Message {i}" for i in range(count)]
        assert len({m.id for m in collected}) == count

    async def test_get_thread_messages_orders_by_insertion_not_timestamp(
        self,
        persisted_thread_doc: ChatThreadDocument,
    ) -> None:
        """A skewed `created_at` cannot reorder a thread; insertion order wins.

        The sort is deliberately single-field on `_id`. Azure Cosmos DB can only
        serve a multi-field sort from a composite index matching it exactly, and
        rejects the query outright otherwise.
        """
        for index, created_at in enumerate(
            (
                datetime(2025, 1, 2, tzinfo=UTC),
                datetime(2025, 1, 1, tzinfo=UTC),
            )
        ):
            await ChatMessageDocument(
                thread_id=persisted_thread_doc.id,
                role="human" if index % 2 == 0 else "ai",
                content=f"Message {index}",
                created_at=created_at,
            ).insert()

        page, _, next_cursor = await get_thread_messages(persisted_thread_doc, limit=10)

        assert [m.content for m in page] == ["Message 0", "Message 1"]
        assert next_cursor is None

    @pytest.mark.parametrize(
        "select_cursor",
        [
            pytest.param(lambda foreign: PydanticObjectId(), id="unknown-cursor"),
            pytest.param(
                lambda foreign: foreign[0].id, id="cursor-from-another-thread"
            ),
        ],
    )
    async def test_get_thread_messages_invalid_cursor_raises_422(
        self,
        persisted_thread_doc: ChatThreadDocument,
        persist_messages: Callable[..., Awaitable[list[ChatMessageDocument]]],
        select_cursor: Callable[[list[ChatMessageDocument]], PydanticObjectId],
    ) -> None:
        """A cursor that is unknown or owned by another thread is rejected with 422."""
        await persist_messages(persisted_thread_doc.id, 2)
        foreign = await persist_messages(PydanticObjectId(), 2)

        with pytest.raises(HTTPException) as exc_info:
            await get_thread_messages(
                persisted_thread_doc, limit=10, before=select_cursor(foreign)
            )

        assert exc_info.value.status_code == 422
        assert exc_info.value.detail == "Invalid pagination cursor."

    async def test_get_thread_messages_excludes_superseded_answers(
        self,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """Answers the user regenerated away from are no longer the conversation."""
        messages, _, _ = await get_thread_messages(persisted_thread_doc, limit=10)

        assert [msg.content for msg in messages] == ["Q1", "A2", "Q2", "A3"]

    async def test_get_thread_messages_annotates_regenerated_answers(
        self,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """A regenerated answer carries its position among its siblings."""
        messages, variants, _ = await get_thread_messages(
            persisted_thread_doc, limit=10
        )
        answer = next(msg for msg in messages if msg.content == "A2")
        position = variants[answer.id]  # type: ignore[index]

        assert position.index == 2
        assert position.count == 2
        assert position.prev_id == branched_thread["a1"].id
        assert position.next_id is None

    async def test_get_thread_messages_omits_pager_for_a_turn_answered_once(
        self,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """A turn answered once has no pager, even in a branched thread."""
        messages, variants, _ = await get_thread_messages(
            persisted_thread_doc, limit=10
        )
        answer = next(msg for msg in messages if msg.content == "A3")

        assert answer.id not in variants

    async def test_get_thread_messages_skips_variant_lookup_when_unbranched(
        self,
        monkeypatch: pytest.MonkeyPatch,
        persisted_thread_doc: ChatThreadDocument,
        persisted_message_docs: list[ChatMessageDocument],
    ) -> None:
        """A thread that has never been regenerated costs no extra query."""
        mock_annotate = AsyncMock()
        monkeypatch.setattr(chat_service, "_annotate_variants", mock_annotate)

        await get_thread_messages(persisted_thread_doc, limit=10)

        mock_annotate.assert_not_awaited()

    async def test_get_thread_messages_accepts_a_cursor_on_a_superseded_message(
        self,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """A cursor held from before a branch switch still pages, rather than 404ing."""
        messages, _, _ = await get_thread_messages(
            persisted_thread_doc, limit=10, before=branched_thread["a1"].id
        )

        assert [msg.content for msg in messages] == ["Q1"]


class TestGetUserThreads:
    """List the threads a user owns, newest first, under optional filters."""

    @pytest.mark.parametrize(
        ("make_filters", "matches"),
        [
            pytest.param(lambda rid: {}, True, id="no-filters"),
            pytest.param(lambda rid: {"repo_id": rid}, True, id="matching-repo-id"),
            pytest.param(
                lambda rid: {"repo_id": PydanticObjectId("000000000000000000000099")},
                False,
                id="other-repo-id",
            ),
            pytest.param(lambda rid: {"search": "test"}, True, id="matching-search"),
            pytest.param(
                lambda rid: {"search": "nonexistent"}, False, id="non-matching-search"
            ),
        ],
    )
    async def test_get_user_threads_filters(
        self,
        user: User,
        repo_id: PydanticObjectId,
        persisted_thread_doc: ChatThreadDocument,
        make_filters: Callable[[PydanticObjectId], _ThreadFilters],
        matches: bool,
    ) -> None:
        """Applies the optional `repo_id` and `search` filters.

        `search` is matched case-insensitively, which a lowercase term run against
        the `"Test thread"` fixture exercises without touching its title.
        """
        result = await get_user_threads(user, **make_filters(repo_id))

        assert [thread.id for thread in result] == (
            [persisted_thread_doc.id] if matches else []
        )

    async def test_get_user_threads_empty_db(self, user: User) -> None:
        """Returns an empty list when no threads exist."""
        result = await get_user_threads(user)

        assert result == []

    async def test_get_user_threads_sorted_by_updated_at(
        self,
        user: User,
        repo_id: PydanticObjectId,
    ) -> None:
        """Threads are sorted by `updated_at` descending."""
        thread1 = await create_thread(repo_id, user, "First message")
        thread2 = await create_thread(repo_id, user, "Second message")

        # Set distinct timestamps to avoid same-millisecond flakiness
        thread1.updated_at = datetime(2025, 1, 1, tzinfo=UTC)
        await thread1.save()
        thread2.updated_at = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=1)
        await thread2.save()

        result = await get_user_threads(user)

        assert len(result) == 2
        assert result[0].id == thread2.id
        assert result[1].id == thread1.id


class TestPersistTurn:
    """Store the question and the answer of a turn with their checkpoint links."""

    @pytest.mark.parametrize(
        "parent_checkpoint_id",
        [
            pytest.param("ckpt-0", id="with-parent"),
            pytest.param(None, id="none-parent"),
        ],
    )
    async def test_persist_turn_inserts_two_messages(
        self,
        monkeypatch: pytest.MonkeyPatch,
        parent_checkpoint_id: str | None,
    ) -> None:
        """Inserts both human and AI messages with the correct checkpoint IDs."""
        mock_msg_instance = MagicMock()
        mock_msg_instance.insert = AsyncMock()
        mock_cls = MagicMock(return_value=mock_msg_instance)
        monkeypatch.setattr(chat_service, "ChatMessageDocument", mock_cls)

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

    async def test_persist_turn_stores_only_the_answer_when_regenerating(
        self,
        persisted_thread_doc: ChatThreadDocument,
    ) -> None:
        """The question is not duplicated: one question, several answers under it."""
        human, ai = await _persist_turn(
            thread_id=str(persisted_thread_doc.id),
            message="Q1",
            response="A2",
            checkpoint_id="cB",
            parent_checkpoint_id=None,
            persist_human=False,
        )

        stored = await ChatMessageDocument.find(
            ChatMessageDocument.thread_id == persisted_thread_doc.id
        ).to_list()

        assert human is None
        assert [doc.id for doc in stored] == [ai.id]

    async def test_persist_turn_stores_the_input_checkpoint(
        self,
        persisted_thread_doc: ChatThreadDocument,
    ) -> None:
        """The turn records where it started, since it cannot be found again later."""
        _, ai = await _persist_turn(
            thread_id=str(persisted_thread_doc.id),
            message="Q1",
            response="A1",
            checkpoint_id="cA",
            parent_checkpoint_id=None,
            input_checkpoint_id="cIn",
        )

        assert ai.input_checkpoint_id == "cIn"


class TestPrepareSafeStream:
    """Resolve a safe re-entry point when the graph was left mid-run."""

    async def test_prepare_safe_stream_walks_back_when_stale(
        self,
        prepare_stream_mocks: Callable[..., tuple[AsyncMock, RunnableConfig]],
    ) -> None:
        """Searches history for input checkpoint and walks back to oldest input."""
        mock_agent, config = prepare_stream_mocks(
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
            pytest.param("prev-ckpt", "prev-ckpt", id="healthy"),
            pytest.param(None, None, id="no-checkpoint"),
        ],
    )
    async def test_prepare_safe_stream_passthrough(
        self,
        prepare_stream_mocks: Callable[..., tuple[AsyncMock, RunnableConfig]],
        checkpoint_id: str | None,
        expected_parent: str | None,
    ) -> None:
        """Returns config unchanged when graph is healthy or brand-new."""
        mock_agent, config = prepare_stream_mocks(
            state_next=(), checkpoint_id=checkpoint_id
        )

        result_config, parent_ckpt = await _prepare_safe_stream(mock_agent, config)

        assert parent_ckpt == expected_parent
        if checkpoint_id is not None:
            assert result_config["configurable"]["checkpoint_id"] == checkpoint_id
        else:
            assert "checkpoint_id" not in result_config["configurable"]

    async def test_prepare_safe_stream_raises_when_no_input_checkpoint(
        self,
        prepare_stream_mocks: Callable[..., tuple[AsyncMock, RunnableConfig]],
    ) -> None:
        """Raises `RuntimeError` when stale but no input checkpoint in history."""
        mock_agent, config = prepare_stream_mocks(
            state_next=("tools",), checkpoint_id="stale-ckpt", metadata_source="loop"
        )

        mock_agent.aget_state_history = MagicMock(return_value=_make_async_gen([]))

        with pytest.raises(RuntimeError, match="no checkpoint with source `input`"):
            await _prepare_safe_stream(mock_agent, config)


class TestResumeChatStream:
    """Continue an existing thread from the answer currently on screen."""

    async def test_resume_chat_stream_yields_events_and_done(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        mock_thread_doc: MagicMock,
        mock_token_event: MagicMock,
    ) -> None:
        """Updates timestamp, yields events, then `done`."""
        monkeypatch.setattr(chat_service, "update_thread_timestamp", AsyncMock())
        monkeypatch.setattr(
            chat_service,
            "stream_agent_response",
            MagicMock(return_value=_make_async_gen([mock_token_event])),
        )

        results = [
            event async for event in resume_chat_stream(mock_thread_doc, "Hello", user)
        ]

        assert results[-1].event == "done"

    async def test_resume_chat_stream_continues_from_the_selected_branch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """The next turn follows the answer on screen, not the newest one written."""
        await select_variant(
            persisted_thread_doc,
            branched_thread["a1"].id,  # type: ignore[arg-type]
            limit=10,
        )
        mock_stream = MagicMock(return_value=_make_async_gen([]))
        monkeypatch.setattr(chat_service, "update_thread_timestamp", AsyncMock())
        monkeypatch.setattr(chat_service, "stream_agent_response", mock_stream)

        [event async for event in resume_chat_stream(persisted_thread_doc, "Q2", user)]

        assert mock_stream.call_args.kwargs["resume_checkpoint_id"] == "cA"

    async def test_resume_chat_stream_does_not_pin_an_unbranched_thread(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        mock_thread_doc: MagicMock,
    ) -> None:
        """A thread that has never been regenerated behaves exactly as before."""
        mock_stream = MagicMock(return_value=_make_async_gen([]))
        monkeypatch.setattr(chat_service, "update_thread_timestamp", AsyncMock())
        monkeypatch.setattr(chat_service, "stream_agent_response", mock_stream)

        [event async for event in resume_chat_stream(mock_thread_doc, "Hello", user)]

        assert mock_stream.call_args.kwargs["resume_checkpoint_id"] is None


class TestRetryMessageStream:
    """Regenerate one answer of a thread from the checkpoint that produced it."""

    async def test_retry_message_stream_forks_from_the_previous_turn(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """An answer mid-thread is regenerated from the checkpoint before it."""
        mock_stream = MagicMock(return_value=_make_async_gen([]))
        monkeypatch.setattr(chat_service, "update_thread_timestamp", AsyncMock())
        monkeypatch.setattr(chat_service, "stream_agent_response", mock_stream)

        [
            event
            async for event in retry_message_stream(
                persisted_thread_doc, branched_thread["a3"], "Q2", user
            )
        ]

        fork = mock_stream.call_args.kwargs["fork"]

        assert fork.checkpoint_id == "cB"
        assert fork.parent_checkpoint_id == "cB"
        assert fork.replay is False
        assert fork.supersedes == branched_thread["a3"].id

    async def test_retry_message_stream_replays_the_first_answer_of_a_thread(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """A first answer has no previous turn, so its own input is replayed."""
        mock_stream = MagicMock(return_value=_make_async_gen([]))
        monkeypatch.setattr(chat_service, "update_thread_timestamp", AsyncMock())
        monkeypatch.setattr(chat_service, "stream_agent_response", mock_stream)

        [
            event
            async for event in retry_message_stream(
                persisted_thread_doc, branched_thread["a2"], "Q1", user
            )
        ]

        fork = mock_stream.call_args.kwargs["fork"]

        assert fork.checkpoint_id == "cIn"
        assert fork.replay is True
        # Carried over, since replaying writes no new input checkpoint.
        assert fork.input_checkpoint_id == "cIn"

    async def test_retry_message_stream_yields_done(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        persisted_thread_doc: ChatThreadDocument,
        mock_token_event: MagicMock,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """Closes the stream like the other chat endpoints."""
        monkeypatch.setattr(chat_service, "update_thread_timestamp", AsyncMock())
        monkeypatch.setattr(
            chat_service,
            "stream_agent_response",
            MagicMock(return_value=_make_async_gen([mock_token_event])),
        )

        results = [
            event
            async for event in retry_message_stream(
                persisted_thread_doc, branched_thread["a3"], "Q2", user
            )
        ]

        assert results[-1].event == "done"


class TestSelectVariant:
    """Switch the conversation onto another answer of a regenerated turn."""

    async def test_select_variant_switches_the_conversation_to_the_other_answer(
        self,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """The chosen answer replaces the one the thread was showing."""
        messages, _, _ = await select_variant(
            persisted_thread_doc,
            branched_thread["a1"].id,  # type: ignore[arg-type]
            limit=10,
        )

        assert [msg.content for msg in messages] == ["Q1", "A1"]

    async def test_select_variant_keeps_the_question_visible(
        self,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """The question is stored once, before every answer to it, and must survive.

        Its `_id` is lower than every answer's, so the sweep that hides the
        other branches has to start below it rather than at the chosen answer.
        """
        messages, _, _ = await select_variant(
            persisted_thread_doc,
            branched_thread["a1"].id,  # type: ignore[arg-type]
            limit=10,
        )

        assert messages[0].content == "Q1"

    async def test_select_variant_restores_the_follow_ups_of_the_chosen_answer(
        self,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """Each answer keeps the conversation that grew out of it."""
        await select_variant(
            persisted_thread_doc,
            branched_thread["a1"].id,  # type: ignore[arg-type]
            limit=10,
        )

        messages, _, _ = await select_variant(
            persisted_thread_doc,
            branched_thread["a2"].id,  # type: ignore[arg-type]
            limit=10,
        )

        assert [msg.content for msg in messages] == ["Q1", "A2", "Q2", "A3"]

    async def test_select_variant_follows_the_branch_to_its_deepest_turn(
        self,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
        persist_branch_message: Callable[..., Awaitable[ChatMessageDocument]],
    ) -> None:
        """The walk continues past the first follow-up to the end of the branch."""
        tid: PydanticObjectId = persisted_thread_doc.id  # type: ignore[assignment]
        await persist_branch_message(tid, "human", "Q3", "cC", "cD")
        await persist_branch_message(tid, "ai", "A5", "cC", "cD")
        await select_variant(
            persisted_thread_doc,
            branched_thread["a1"].id,  # type: ignore[arg-type]
            limit=10,
        )

        messages, _, _ = await select_variant(
            persisted_thread_doc,
            branched_thread["a2"].id,  # type: ignore[arg-type]
            limit=10,
        )

        assert [msg.content for msg in messages] == [
            "Q1",
            "A2",
            "Q2",
            "A3",
            "Q3",
            "A5",
        ]

    async def test_select_variant_is_idempotent(
        self,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """Selecting the answer already shown changes nothing."""
        messages, _, _ = await select_variant(
            persisted_thread_doc,
            branched_thread["a2"].id,  # type: ignore[arg-type]
            limit=10,
        )

        assert [msg.content for msg in messages] == ["Q1", "A2", "Q2", "A3"]

    async def test_select_variant_stops_at_an_answer_with_no_checkpoint(
        self,
        persisted_thread_doc: ChatThreadDocument,
        persist_branch_message: Callable[..., Awaitable[ChatMessageDocument]],
    ) -> None:
        """Nothing can have been stored under it, so it ends the branch."""
        tid: PydanticObjectId = persisted_thread_doc.id  # type: ignore[assignment]
        await persist_branch_message(tid, "human", "Q1", None, "cA", True, "cIn")
        await persist_branch_message(tid, "ai", "A1", None, "cA", False, "cIn")
        orphan = await persist_branch_message(tid, "ai", "A2", None, None, False, "cIn")

        messages, _, _ = await select_variant(
            persisted_thread_doc,
            orphan.id,  # type: ignore[arg-type]
            limit=10,
        )

        assert [msg.content for msg in messages] == ["Q1", "A2"]

    async def test_select_variant_does_not_reorder_the_thread_list(
        self,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """Reading an answer that already exists adds nothing to the conversation."""
        before = persisted_thread_doc.updated_at

        await select_variant(
            persisted_thread_doc,
            branched_thread["a1"].id,  # type: ignore[arg-type]
            limit=10,
        )
        reloaded = await ChatThreadDocument.get(persisted_thread_doc.id)

        assert reloaded is not None
        assert reloaded.updated_at == before

    async def test_select_variant_rejects_an_unknown_message(
        self,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """A message that is not an answer in this thread is not found."""
        with pytest.raises(HTTPException) as exc_info:
            await select_variant(persisted_thread_doc, PydanticObjectId(), limit=10)

        assert exc_info.value.status_code == 404


class TestStreamAgentResponse:
    """Stream one agent turn as SSE events and persist what it produced."""

    @pytest.mark.parametrize(
        "prepare_kwargs",
        [
            pytest.param(
                {"return_value": (_STREAM_CONFIG, "prev-ckpt")},
                id="agent-stream-raises",
            ),
            pytest.param(
                {"side_effect": RuntimeError("recovery failed")},
                id="prepare-safe-stream-raises",
            ),
        ],
    )
    async def test_stream_agent_response_yields_error_on_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        collect_stream_events: Callable[[], Awaitable[list[ServerSentEvent]]],
        prepare_kwargs: dict[str, tuple[RunnableConfig, str] | RuntimeError],
    ) -> None:
        """Yields a single error event when resolving or streaming fails.

        The agent's `astream` is armed to raise on both rows; on the second it is
        never reached, because resolving the stream fails first.
        """
        mock_agent = AsyncMock()
        mock_agent.astream = _async_iter_error(RuntimeError("boom"))
        monkeypatch.setattr(
            chat_service, "get_agent", MagicMock(return_value=mock_agent)
        )
        monkeypatch.setattr(
            chat_service, "_prepare_safe_stream", AsyncMock(**prepare_kwargs)
        )

        results = await collect_stream_events()

        assert len(results) == 1
        assert results[0].event == "error"
        assert results[0].data == {
            "error": "An error occurred while generating the response."
        }

    async def test_stream_agent_response_yields_error_on_persist_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_agent: Callable[..., CompiledStateGraph],  # type: ignore[type-arg]
        collect_stream_events: Callable[[], Awaitable[list[ServerSentEvent]]],
    ) -> None:
        """Yields an error event when persisting messages fails."""
        agent = fake_agent(["Hello"])
        monkeypatch.setattr(chat_service, "get_agent", MagicMock(return_value=agent))
        monkeypatch.setattr(
            chat_service,
            "_persist_turn",
            AsyncMock(side_effect=RuntimeError("DB down")),
        )

        results = await collect_stream_events()

        error_events = [e for e in results if e.event == "error"]
        assert len(error_events) == 1
        assert error_events[0].data == {"error": "Failed to save the conversation."}

    async def test_stream_agent_response_yields_token_events(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_agent: Callable[..., CompiledStateGraph],  # type: ignore[type-arg]
        collect_stream_events: Callable[[], Awaitable[list[ServerSentEvent]]],
    ) -> None:
        """Yields token SSE events from agent stream."""
        agent = fake_agent(["Hello world"])
        monkeypatch.setattr(chat_service, "get_agent", MagicMock(return_value=agent))
        monkeypatch.setattr(chat_service, "_persist_turn", AsyncMock())

        results = await collect_stream_events()

        token_events = [e for e in results if e.data and "token" in e.data]
        assert len(token_events) > 0

        full_response = "".join(e.data["token"] for e in token_events)
        assert "Hello world" in full_response

    async def test_stream_agent_response_uses_stream_config(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_agent: Callable[..., CompiledStateGraph],  # type: ignore[type-arg]
        collect_stream_events: Callable[[], Awaitable[list[ServerSentEvent]]],
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
        spy = MagicMock(wraps=agent.astream)
        monkeypatch.setattr(chat_service, "get_agent", MagicMock(return_value=agent))
        monkeypatch.setattr(chat_service, "_persist_turn", AsyncMock())
        monkeypatch.setattr(
            chat_service,
            "_prepare_safe_stream",
            AsyncMock(return_value=(resolved_config, "prev-ckpt")),
        )
        monkeypatch.setattr(agent, "astream", spy)

        await collect_stream_events()

        call_args, call_kwargs = spy.call_args
        assert call_args[0] == {"messages": [{"role": "user", "content": "Hi"}]}
        assert call_kwargs["config"] is resolved_config

    async def test_stream_agent_response_reenters_at_the_fork_checkpoint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        persisted_thread_doc: ChatThreadDocument,
        fake_agent: Callable[..., CompiledStateGraph],  # type: ignore[type-arg]
    ) -> None:
        """A regenerating run resumes the graph from the given checkpoint."""
        agent = fake_agent(["A2"])
        fork = ForkPoint(checkpoint_id="cPrev", parent_checkpoint_id="cPrev")
        spy = MagicMock(wraps=agent.astream)
        monkeypatch.setattr(chat_service, "get_agent", MagicMock(return_value=agent))
        monkeypatch.setattr(chat_service, "_persist_turn", AsyncMock())
        monkeypatch.setattr(agent, "astream", spy)

        await _collect_fork_events(str(persisted_thread_doc.id), fork, user)

        call_args, call_kwargs = spy.call_args
        assert call_args[0] == {"messages": [{"role": "user", "content": "Q1"}]}
        assert call_kwargs["config"]["configurable"]["checkpoint_id"] == "cPrev"

    async def test_stream_agent_response_skips_recovery_when_forking(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        persisted_thread_doc: ChatThreadDocument,
        fake_agent: Callable[..., CompiledStateGraph],  # type: ignore[type-arg]
    ) -> None:
        """The fork point is taken verbatim, so the variant joins the right group."""
        agent = fake_agent(["A2"])
        fork = ForkPoint(checkpoint_id="cPrev", parent_checkpoint_id="cPrev")
        mock_prepare = AsyncMock()
        monkeypatch.setattr(chat_service, "get_agent", MagicMock(return_value=agent))
        monkeypatch.setattr(chat_service, "_persist_turn", AsyncMock())
        monkeypatch.setattr(chat_service, "_prepare_safe_stream", mock_prepare)

        await _collect_fork_events(str(persisted_thread_doc.id), fork, user)

        mock_prepare.assert_not_awaited()

    async def test_stream_agent_response_replays_a_root_turn(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        persisted_thread_doc: ChatThreadDocument,
        fake_agent: Callable[..., CompiledStateGraph],  # type: ignore[type-arg]
    ) -> None:
        """The first turn of a thread is replayed rather than sent again."""
        agent = fake_agent(["A1", "A2"])
        thread_id = str(persisted_thread_doc.id)
        monkeypatch.setattr(chat_service, "get_agent", MagicMock(return_value=agent))
        monkeypatch.setattr(chat_service, "_persist_turn", AsyncMock())

        await _collect_stream_events("Q1", thread_id, "rid", user)
        state = await agent.aget_state({"configurable": {"thread_id": thread_id}})
        input_checkpoint_id = [
            snapshot.config["configurable"]["checkpoint_id"]
            async for snapshot in agent.aget_state_history(
                {"configurable": {"thread_id": thread_id}},
                filter={"source": "input"},
            )
        ][0]
        fork = ForkPoint(
            checkpoint_id=input_checkpoint_id,
            parent_checkpoint_id=None,
            replay=True,
            input_checkpoint_id=input_checkpoint_id,
        )
        spy = MagicMock(wraps=agent.astream)
        monkeypatch.setattr(agent, "astream", spy)
        await _collect_fork_events(thread_id, fork, user)

        assert spy.call_args[0][0] is None
        assert [msg.text for msg in state.values["messages"]] == ["Q1", "A1"]

    async def test_stream_agent_response_reproduces_the_context_when_regenerating(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        persisted_thread_doc: ChatThreadDocument,
        fake_agent: Callable[..., CompiledStateGraph],  # type: ignore[type-arg]
    ) -> None:
        """The model sees the same conversation it saw the first time, once only.

        The regenerated turn re-sends the question from the *previous* turn's
        checkpoint, so the graph ends up with one copy of it, not two.
        """
        agent = fake_agent(["A1", "A2", "A2-again"])
        thread_id = str(persisted_thread_doc.id)
        config = {"configurable": {"thread_id": thread_id}}
        monkeypatch.setattr(chat_service, "get_agent", MagicMock(return_value=agent))
        monkeypatch.setattr(chat_service, "_persist_turn", AsyncMock())

        await _collect_stream_events("Q1", thread_id, "rid", user)
        first_turn = (await agent.aget_state(config)).config["configurable"][  # type: ignore[arg-type]
            "checkpoint_id"
        ]
        await _collect_stream_events("Q2", thread_id, "rid", user)

        fork = ForkPoint(checkpoint_id=first_turn, parent_checkpoint_id=first_turn)
        await _collect_fork_events(thread_id, fork, user, message="Q2")

        state = await agent.aget_state(config)  # type: ignore[arg-type]
        assert [msg.text for msg in state.values["messages"]] == [
            "Q1",
            "A1",
            "Q2",
            "A2-again",
        ]

    async def test_stream_agent_response_supersedes_the_regenerated_answer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        persisted_thread_doc: ChatThreadDocument,
        fake_agent: Callable[..., CompiledStateGraph],  # type: ignore[type-arg]
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """The old answer and everything after it leave the conversation."""
        agent = fake_agent(["A4"])
        fork = ForkPoint(
            checkpoint_id="cB",
            parent_checkpoint_id="cB",
            supersedes=branched_thread["a3"].id,
        )
        monkeypatch.setattr(chat_service, "get_agent", MagicMock(return_value=agent))

        await _collect_fork_events(
            str(persisted_thread_doc.id), fork, user, message="Q2"
        )

        messages, _, _ = await get_thread_messages(persisted_thread_doc, limit=10)

        assert [msg.content for msg in messages] == ["Q1", "A2", "Q2", "A4"]

    async def test_stream_agent_response_detaches_the_follow_ups_it_regenerates_past(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        persisted_thread_doc: ChatThreadDocument,
        fake_agent: Callable[..., CompiledStateGraph],  # type: ignore[type-arg]
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """Regenerating mid-conversation takes the turns that replied to it too."""
        agent = fake_agent(["A2-again"])
        fork = ForkPoint(
            checkpoint_id="cIn",
            parent_checkpoint_id=None,
            supersedes=branched_thread["a2"].id,
        )
        monkeypatch.setattr(chat_service, "get_agent", MagicMock(return_value=agent))

        await _collect_fork_events(str(persisted_thread_doc.id), fork, user)

        messages, _, _ = await get_thread_messages(persisted_thread_doc, limit=10)

        assert [msg.content for msg in messages] == ["Q1", "A2-again"]

    async def test_stream_agent_response_reports_the_new_variant_position(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        persisted_thread_doc: ChatThreadDocument,
        fake_agent: Callable[..., CompiledStateGraph],  # type: ignore[type-arg]
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """The client learns the pager position without refetching the thread."""
        agent = fake_agent(["A4"])
        fork = ForkPoint(
            checkpoint_id="cB",
            parent_checkpoint_id="cB",
            supersedes=branched_thread["a3"].id,
        )
        monkeypatch.setattr(chat_service, "get_agent", MagicMock(return_value=agent))

        events = await _collect_fork_events(
            str(persisted_thread_doc.id), fork, user, message="Q2"
        )

        saved = next(event for event in events if event.event == "message_saved")

        assert saved.data["variant_index"] == 2
        assert saved.data["variant_count"] == 2
        assert saved.data["prev_variant_id"] == str(branched_thread["a3"].id)

    async def test_stream_agent_response_marks_the_thread_as_branched(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        persisted_thread_doc: ChatThreadDocument,
        fake_agent: Callable[..., CompiledStateGraph],  # type: ignore[type-arg]
        persist_branch_message: Callable[..., Awaitable[ChatMessageDocument]],
    ) -> None:
        """Later reads know to look for variants."""
        tid: PydanticObjectId = persisted_thread_doc.id  # type: ignore[assignment]
        await persist_branch_message(tid, "human", "Q1", None, "cA", True, "cIn")
        answer = await persist_branch_message(tid, "ai", "A1", None, "cA", True, "cIn")
        agent = fake_agent(["A2"])
        fork = ForkPoint(
            checkpoint_id="cIn",
            parent_checkpoint_id=None,
            supersedes=answer.id,
        )
        monkeypatch.setattr(chat_service, "get_agent", MagicMock(return_value=agent))

        await _collect_fork_events(str(tid), fork, user)

        reloaded = await ChatThreadDocument.get(tid)

        assert reloaded is not None
        assert reloaded.has_variants is True

    async def test_stream_agent_response_keeps_the_old_answer_when_the_stream_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: User,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """A failed regeneration leaves the thread showing the answer it had."""
        mock_agent = MagicMock()
        mock_agent.astream = MagicMock(side_effect=RuntimeError("model down"))
        fork = ForkPoint(
            checkpoint_id="cB",
            parent_checkpoint_id="cB",
            supersedes=branched_thread["a3"].id,
        )
        monkeypatch.setattr(
            chat_service, "get_agent", MagicMock(return_value=mock_agent)
        )

        events = await _collect_fork_events(
            str(persisted_thread_doc.id), fork, user, message="Q2"
        )

        messages, _, _ = await get_thread_messages(persisted_thread_doc, limit=10)

        assert [event.event for event in events] == ["error"]
        assert [msg.content for msg in messages] == ["Q1", "A2", "Q2", "A3"]


class TestTruncateTitle:
    """Cut a thread title to the configured length on a word boundary."""

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            pytest.param("Hello", "Hello", id="short"),
            pytest.param(
                "a" * chat_settings.TITLE_MAX_LEN,
                "a" * chat_settings.TITLE_MAX_LEN,
                id="exactly-at-limit",
            ),
            pytest.param(
                "word " * chat_settings.TITLE_MAX_LEN,
                " ".join(["word"] * ((chat_settings.TITLE_MAX_LEN - 3) // 5)) + "...",
                id="truncated-on-word-boundary",
            ),
        ],
    )
    def test_truncate_title(self, message: str, expected: str) -> None:
        """Returns messages within the limit unchanged, else cuts on a word boundary.

        The truncated expectation is derived rather than hardcoded: `_truncate_title`
        keeps `TITLE_MAX_LEN - 3` characters before appending the ellipsis and then
        drops the trailing partial word, so `"word "` (5 characters) fits
        `(TITLE_MAX_LEN - 3) // 5` times. Bumping the setting must not fail this test.
        """
        assert _truncate_title(message) == expected


class TestUpdateThreadTimestamp:
    """Stamp a thread as active so it sorts to the top of the list."""

    async def test_update_thread_timestamp_persists(
        self,
        persisted_thread_doc: ChatThreadDocument,
    ) -> None:
        """Updated timestamp is persisted to the database."""
        # Set a known old timestamp to avoid microsecond-precision issues with mongomock
        persisted_thread_doc.updated_at = datetime(2020, 1, 1, tzinfo=UTC)
        await persisted_thread_doc.save()

        await update_thread_timestamp(persisted_thread_doc)

        refreshed = await ChatThreadDocument.get(persisted_thread_doc.id)
        assert refreshed is not None
        assert refreshed.updated_at > datetime(2020, 1, 1)


class TestUpdateThreadTitle:
    """Persist a new title on an existing thread."""

    async def test_update_thread_title_persists(
        self,
        persisted_thread_doc: ChatThreadDocument,
    ) -> None:
        """Updated title is persisted to the database."""
        await update_thread_title(persisted_thread_doc, "New Title")

        refreshed = await ChatThreadDocument.get(persisted_thread_doc.id)
        assert refreshed is not None
        assert refreshed.title == "New Title"


class TestVariantPositions:
    """Number the answers of a regenerated turn for the client's pager."""

    def test_variant_positions_ignores_a_question_answered_once(self) -> None:
        """A single answer has nothing to page through."""
        assert _variant_positions([_node("000000000000000000000001")]) == {}

    def test_variant_positions_numbers_by_insertion_order(self) -> None:
        """Numbering follows `_id`, so it matches the order answers were generated."""
        first = _node("000000000000000000000001")
        second = _node("000000000000000000000002")
        third = _node("000000000000000000000003")

        positions = _variant_positions([third, first, second])

        assert positions[first.id].index == 1
        assert positions[second.id].index == 2
        assert positions[third.id].index == 3
        assert positions[second.id].count == 3
        assert positions[second.id].prev_id == first.id
        assert positions[second.id].next_id == third.id
        assert positions[first.id].prev_id is None
        assert positions[third.id].next_id is None
