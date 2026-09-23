"""Unit tests for the chat router.

This module tests the chat router using `httpx.AsyncClient` with
dependency overrides, against a mongomock database.
"""

from collections.abc import Awaitable, Callable

import httpx
import pytest

from app.auth.dependencies import get_current_user
from app.auth.schemas import User
from app.chat.config import chat_settings
from app.chat.dependencies import get_verified_chat_thread
from app.chat.models import ChatMessageDocument, ChatThreadDocument

pytestmark = pytest.mark.usefixtures("_override_deps")


@pytest.fixture
def _override_deps(
    monkeypatch: pytest.MonkeyPatch,
    user: User,
    persisted_thread_doc: ChatThreadDocument,
) -> None:
    """Override the FastAPI auth and thread dependencies for chat HTTP tests."""
    from app.main import app

    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: user)
    monkeypatch.setitem(
        app.dependency_overrides,
        get_verified_chat_thread,
        lambda: persisted_thread_doc,
    )


class TestGetChatMessagesEndpoint:
    """`GET /chat/{chat_id}` — serve a page of a thread's conversation."""

    async def test_get_chat_messages_applies_default_page_size(
        self,
        mock_client: httpx.AsyncClient,
        persisted_thread_doc: ChatThreadDocument,
        persist_messages: Callable[..., Awaitable[list[ChatMessageDocument]]],
    ) -> None:
        """Omitting `limit` returns the newest page, not the whole history."""
        total = chat_settings.MESSAGES_PAGE_SIZE + 5
        await persist_messages(persisted_thread_doc.id, total)

        resp = await mock_client.get(f"/chat/{persisted_thread_doc.id}")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) == chat_settings.MESSAGES_PAGE_SIZE
        assert data["messages"][0]["content"] == "Message 5"
        assert data["messages"][-1]["content"] == f"Message {total - 1}"
        assert data["next_cursor"] == data["messages"][0]["id"]
        assert data["chat_id"] == str(persisted_thread_doc.id)

    async def test_get_chat_messages_honours_limit(
        self,
        mock_client: httpx.AsyncClient,
        persisted_thread_doc: ChatThreadDocument,
        persist_messages: Callable[..., Awaitable[list[ChatMessageDocument]]],
    ) -> None:
        """An explicit `limit` bounds the page."""
        await persist_messages(persisted_thread_doc.id, 10)

        resp = await mock_client.get(
            f"/chat/{persisted_thread_doc.id}", params={"limit": 4}
        )

        assert resp.status_code == 200
        data = resp.json()
        assert [m["content"] for m in data["messages"]] == [
            "Message 6",
            "Message 7",
            "Message 8",
            "Message 9",
        ]
        assert "next_cursor" in data

    async def test_get_chat_messages_before_returns_older_page(
        self,
        mock_client: httpx.AsyncClient,
        persisted_thread_doc: ChatThreadDocument,
        persist_messages: Callable[..., Awaitable[list[ChatMessageDocument]]],
    ) -> None:
        """`before` walks back to the preceding page."""
        await persist_messages(persisted_thread_doc.id, 10)

        first = await mock_client.get(
            f"/chat/{persisted_thread_doc.id}", params={"limit": 4}
        )
        cursor = first.json()["next_cursor"]

        resp = await mock_client.get(
            f"/chat/{persisted_thread_doc.id}", params={"limit": 4, "before": cursor}
        )

        assert resp.status_code == 200
        data = resp.json()
        assert [m["content"] for m in data["messages"]] == [
            "Message 2",
            "Message 3",
            "Message 4",
            "Message 5",
        ]
        assert "next_cursor" in data

    async def test_get_chat_messages_omits_cursor_on_last_page(
        self,
        mock_client: httpx.AsyncClient,
        persisted_thread_doc: ChatThreadDocument,
        persist_messages: Callable[..., Awaitable[list[ChatMessageDocument]]],
    ) -> None:
        """`next_cursor` is absent once the history is exhausted."""
        await persist_messages(persisted_thread_doc.id, 3)

        resp = await mock_client.get(f"/chat/{persisted_thread_doc.id}")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) == 3
        # `response_model_exclude_none=True` drops the null cursor.
        assert "next_cursor" not in data

    async def test_get_chat_messages_rejects_oversized_limit(
        self,
        mock_client: httpx.AsyncClient,
        persisted_thread_doc: ChatThreadDocument,
    ) -> None:
        """A `limit` above the configured maximum is rejected."""
        resp = await mock_client.get(
            f"/chat/{persisted_thread_doc.id}",
            params={"limit": chat_settings.MESSAGES_MAX_PAGE_SIZE + 1},
        )

        assert resp.status_code == 422

    async def test_get_chat_messages_rejects_non_positive_limit(
        self,
        mock_client: httpx.AsyncClient,
        persisted_thread_doc: ChatThreadDocument,
    ) -> None:
        """A `limit` below 1 is rejected."""
        resp = await mock_client.get(
            f"/chat/{persisted_thread_doc.id}", params={"limit": 0}
        )

        assert resp.status_code == 422

    async def test_get_chat_messages_rejects_unknown_cursor(
        self,
        mock_client: httpx.AsyncClient,
        persisted_thread_doc: ChatThreadDocument,
        persist_messages: Callable[..., Awaitable[list[ChatMessageDocument]]],
    ) -> None:
        """A cursor that is not a message of this thread is rejected."""
        await persist_messages(persisted_thread_doc.id, 3)

        resp = await mock_client.get(
            f"/chat/{persisted_thread_doc.id}",
            params={"before": "000000000000000000000099"},
        )

        assert resp.status_code == 422

    async def test_get_chat_messages_omits_pager_fields_for_an_unbranched_thread(
        self,
        mock_client: httpx.AsyncClient,
        persisted_thread_doc: ChatThreadDocument,
        persist_messages: Callable[..., Awaitable[list[ChatMessageDocument]]],
    ) -> None:
        """A thread that has never been regenerated serves the payload it always did."""
        await persist_messages(persisted_thread_doc.id, 2)

        resp = await mock_client.get(f"/chat/{persisted_thread_doc.id}")

        assert resp.status_code == 200
        # `response_model_exclude_none=True` drops the unset pager fields.
        assert set(resp.json()["messages"][0]) == {"id", "role", "content"}

    async def test_get_chat_messages_carries_the_pager_for_a_regenerated_answer(
        self,
        mock_client: httpx.AsyncClient,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """A regenerated answer reaches the client with its place among its siblings."""
        resp = await mock_client.get(f"/chat/{persisted_thread_doc.id}")

        assert resp.status_code == 200
        answer = next(m for m in resp.json()["messages"] if m["content"] == "A2")
        assert answer["variant_index"] == 2
        assert answer["variant_count"] == 2
        assert answer["prev_variant_id"] == str(branched_thread["a1"].id)
        assert "next_variant_id" not in answer


class TestRetryChatMessageEndpoint:
    """`POST /chat/{chat_id}/retry` — regenerate an answer already on the thread."""

    async def test_retry_chat_message_rejects_an_answer_off_the_conversation(
        self,
        mock_client: httpx.AsyncClient,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """The guard is enforced server-side, not by hiding the button."""
        resp = await mock_client.post(
            f"/chat/{persisted_thread_doc.id}/retry",
            json={"message_id": str(branched_thread["a1"].id)},
        )

        assert resp.status_code == 409

    async def test_retry_chat_message_rejects_an_unknown_message(
        self,
        mock_client: httpx.AsyncClient,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """A rejection is a real status, not a stream carrying an error event."""
        resp = await mock_client.post(
            f"/chat/{persisted_thread_doc.id}/retry",
            json={"message_id": "000000000000000000000099"},
        )

        assert resp.status_code == 404


class TestSelectChatVariantEndpoint:
    """`POST /chat/{chat_id}/variant` — switch the thread to another answer."""

    async def test_select_chat_variant_returns_the_switched_conversation(
        self,
        mock_client: httpx.AsyncClient,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """The caller can render the result without a second request."""
        resp = await mock_client.post(
            f"/chat/{persisted_thread_doc.id}/variant",
            json={"message_id": str(branched_thread["a1"].id)},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert [m["content"] for m in data["messages"]] == ["Q1", "A1"]
        assert data["messages"][1]["variant_index"] == 1
        assert data["messages"][1]["variant_count"] == 2
        assert data["messages"][1]["next_variant_id"] == str(branched_thread["a2"].id)

    async def test_select_chat_variant_rejects_an_unknown_message(
        self,
        mock_client: httpx.AsyncClient,
        persisted_thread_doc: ChatThreadDocument,
        branched_thread: dict[str, ChatMessageDocument],
    ) -> None:
        """Switching to something that is not an answer in this thread is a 404."""
        resp = await mock_client.post(
            f"/chat/{persisted_thread_doc.id}/variant",
            json={"message_id": "000000000000000000000099"},
        )

        assert resp.status_code == 404
