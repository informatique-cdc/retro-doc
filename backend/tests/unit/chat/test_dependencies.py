"""Unit tests for chat dependencies.

This module tests the chat dependencies that resolve a thread and a retry
target before a request reaches the router.
"""

from collections.abc import Awaitable, Callable

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException, status

from app.auth.schemas import User
from app.chat.dependencies import get_verified_chat_thread, get_verified_retry_target
from app.chat.models import ChatMessageDocument, ChatThreadDocument
from app.chat.schemas import RetryMessageRequest


class TestGetVerifiedChatThread:
    """Resolve a chat thread and confirm it belongs to the current user."""

    async def test_get_verified_chat_thread_raises_404_for_other_user(
        self,
        persisted_thread_doc: ChatThreadDocument,
        user_alt: User,
    ) -> None:
        """Raises HTTP 404 when the thread belongs to another user."""
        with pytest.raises(HTTPException) as exc_info:
            await get_verified_chat_thread(
                persisted_thread_doc.id,  # type: ignore[arg-type]
                user_alt,
            )

        assert exc_info.value.status_code == 404

    async def test_get_verified_chat_thread_raises_404_when_not_found(
        self,
        user: User,
    ) -> None:
        """Raises HTTP 404 when the thread does not exist."""
        fake_id = PydanticObjectId("000000000000000000000099")

        with pytest.raises(HTTPException) as exc_info:
            await get_verified_chat_thread(fake_id, user)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Chat thread not found."

    async def test_get_verified_chat_thread_returns_when_exists(
        self,
        user: User,
        persisted_thread_doc: ChatThreadDocument,
    ) -> None:
        """Returns the thread when it exists and belongs to the user."""
        result = await get_verified_chat_thread(
            persisted_thread_doc.id,  # type: ignore[arg-type]
            user,
        )

        assert result.id == persisted_thread_doc.id


class TestGetVerifiedRetryTarget:
    """Resolve the answer to regenerate together with the question it answered."""

    async def test_get_verified_retry_target_returns_the_answer_and_its_question(
        self,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """Resolves the answer together with the question it was given to."""
        answer_id: PydanticObjectId = branched_thread["a3"].id  # type: ignore[assignment]

        target, prompt = await get_verified_retry_target(
            RetryMessageRequest(message_id=answer_id), persisted_thread_doc
        )

        assert target.id == branched_thread["a3"].id
        assert prompt == "Q2"

    async def test_get_verified_retry_target_rejects_a_human_message(
        self,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """Only answers can be regenerated."""
        question_id: PydanticObjectId = branched_thread["q2"].id  # type: ignore[assignment]

        with pytest.raises(HTTPException) as exc_info:
            await get_verified_retry_target(
                RetryMessageRequest(message_id=question_id), persisted_thread_doc
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    async def test_get_verified_retry_target_rejects_a_message_from_another_thread(
        self,
        persisted_thread_doc: ChatThreadDocument,
        user: User,
        repo_id: PydanticObjectId,
        persist_branch_message: Callable[..., Awaitable[ChatMessageDocument]],
    ) -> None:
        """An answer in another thread is not found, even though the ID exists."""
        other = await ChatThreadDocument(
            user_id=user.uid, repo_id=repo_id, title="Other"
        ).insert()
        foreign = await persist_branch_message(
            other.id, "ai", "A", None, "c1", True, "cIn"
        )
        foreign_id: PydanticObjectId = foreign.id  # type: ignore[assignment]

        with pytest.raises(HTTPException) as exc_info:
            await get_verified_retry_target(
                RetryMessageRequest(message_id=foreign_id), persisted_thread_doc
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    async def test_get_verified_retry_target_accepts_an_answer_already_followed_up(
        self,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """An answer needs to be on the conversation, not at the end of it."""
        answer_id: PydanticObjectId = branched_thread["a2"].id  # type: ignore[assignment]

        target, prompt = await get_verified_retry_target(
            RetryMessageRequest(message_id=answer_id), persisted_thread_doc
        )

        assert target.id == branched_thread["a2"].id
        assert prompt == "Q1"

    async def test_get_verified_retry_target_rejects_a_superseded_answer(
        self,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """An answer already regenerated away from is not on the conversation."""
        answer_id: PydanticObjectId = branched_thread["a1"].id  # type: ignore[assignment]

        with pytest.raises(HTTPException) as exc_info:
            await get_verified_retry_target(
                RetryMessageRequest(message_id=answer_id), persisted_thread_doc
            )

        assert exc_info.value.status_code == status.HTTP_409_CONFLICT

    async def test_get_verified_retry_target_rejects_a_first_answer_with_no_checkpoints(
        self,
        persisted_thread_doc: ChatThreadDocument,
        persist_branch_message: Callable[..., Awaitable[ChatMessageDocument]],
    ) -> None:
        """A first answer stored before checkpoint tracking has nothing to replay."""
        tid: PydanticObjectId = persisted_thread_doc.id  # type: ignore[assignment]
        await persist_branch_message(tid, "human", "Q1")
        answer = await persist_branch_message(tid, "ai", "A1")
        answer_id: PydanticObjectId = answer.id  # type: ignore[assignment]

        with pytest.raises(HTTPException) as exc_info:
            await get_verified_retry_target(
                RetryMessageRequest(message_id=answer_id), persisted_thread_doc
            )

        assert exc_info.value.status_code == status.HTTP_409_CONFLICT
