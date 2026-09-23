"""Unit test configuration for chat.

This module provides database-backed fixtures (via mongomock)
shared by several chat test modules.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal

import pytest
from beanie import PydanticObjectId

from app.auth.schemas import User
from app.chat.models import ChatMessageDocument, ChatThreadDocument


@pytest.fixture
async def branched_thread(
    persisted_thread_doc: ChatThreadDocument,
    persist_branch_message: Callable[..., Awaitable[ChatMessageDocument]],
) -> dict[str, ChatMessageDocument]:
    """A thread whose first answer was regenerated, then followed up.

    Mirrors what the service writes when an answer is regenerated: the
    question is stored once, both answers hang off it, and the conversation
    continues from the selected one.

        Q1 ─┬─ A1  (superseded)
            └─ A2 ── Q2 ── A3
    """
    persisted_thread_doc.has_variants = True
    await persisted_thread_doc.save()

    tid = persisted_thread_doc.id
    return {
        "q1": await persist_branch_message(tid, "human", "Q1", None, "cA", True, "cIn"),
        "a1": await persist_branch_message(tid, "ai", "A1", None, "cA", False, "cIn"),
        "a2": await persist_branch_message(tid, "ai", "A2", None, "cB", True, "cIn"),
        "q2": await persist_branch_message(tid, "human", "Q2", "cB", "cC"),
        "a3": await persist_branch_message(tid, "ai", "A3", "cB", "cC"),
    }


@pytest.fixture
def chat_thread_doc(user: User, repo_id: PydanticObjectId) -> ChatThreadDocument:
    """A `ChatThreadDocument` with deterministic fields."""
    return ChatThreadDocument(
        user_id=user.uid,
        repo_id=repo_id,
        title="Test thread",
    )


@pytest.fixture
def persist_branch_message() -> Callable[..., Awaitable[ChatMessageDocument]]:
    """Factory that persists one message with explicit branch links.

    Unlike `persist_messages`, the checkpoint links are given rather than
    left empty, so the stored messages form a tree the branch logic can
    actually walk.
    """

    async def _factory(
        thread_id: PydanticObjectId,
        role: Literal["human", "ai"],
        content: str,
        parent_checkpoint_id: str | None = None,
        checkpoint_id: str | None = None,
        active: bool = True,
        input_checkpoint_id: str | None = None,
    ) -> ChatMessageDocument:
        return await ChatMessageDocument(
            thread_id=thread_id,
            role=role,
            content=content,
            parent_checkpoint_id=parent_checkpoint_id,
            checkpoint_id=checkpoint_id,
            input_checkpoint_id=input_checkpoint_id,
            active=active,
        ).insert()

    return _factory


@pytest.fixture
def persist_messages() -> Callable[..., Awaitable[list[ChatMessageDocument]]]:
    """Factory that persists `count` numbered messages of a thread, oldest first.

    An explicit `created_at` gives every message the same timestamp, which is what
    a turn written by `_persist_turn` looks like.
    """

    async def _factory(
        thread_id: PydanticObjectId,
        count: int,
        created_at: datetime | None = None,
    ) -> list[ChatMessageDocument]:
        messages = [
            ChatMessageDocument(
                thread_id=thread_id,
                role="human" if index % 2 == 0 else "ai",
                content=f"Message {index}",
            )
            for index in range(count)
        ]
        for message in messages:
            if created_at is not None:
                message.created_at = created_at
            await message.insert()
        return messages

    return _factory


@pytest.fixture
async def persisted_thread_doc(
    user: User, repo_id: PydanticObjectId
) -> ChatThreadDocument:
    """A persisted `ChatThreadDocument`."""
    thread = ChatThreadDocument(
        user_id=user.uid,
        repo_id=repo_id,
        title="Test thread",
    )
    await thread.insert()
    return thread
