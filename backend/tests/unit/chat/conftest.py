"""Unit test configuration for chat.

This module provides mock-based fixtures for isolated tests,
database-backed fixtures (via mongomock) for persistence tests,
and LangChain fake fixtures for testing agent logic without API calls.
"""

from collections.abc import Callable
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from app.auth.schemas import User
from app.chat.models import ChatMessageDocument, ChatThreadDocument
from app.chat.schemas import ChatContext


@pytest.fixture
def chat_id() -> PydanticObjectId:
    """A fixed chat thread ID for tests."""
    return PydanticObjectId("000000000000000000000010")


@pytest.fixture
def chat_thread_doc(user: User, repo_id: PydanticObjectId) -> ChatThreadDocument:
    """A `ChatThreadDocument` with deterministic fields."""
    return ChatThreadDocument(
        user_id=user.uid,
        repo_id=repo_id,
        title="Test thread",
    )


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
    thread.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    thread.updated_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
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
async def persisted_thread_doc(
    chat_thread_doc: ChatThreadDocument,
) -> ChatThreadDocument:
    """A `ChatThreadDocument` persisted in mongomock."""
    await chat_thread_doc.insert()
    return chat_thread_doc
