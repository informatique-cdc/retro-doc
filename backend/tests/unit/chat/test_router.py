"""Unit tests for chat router.

This module tests the chat router using `httpx.AsyncClient` with
dependency overrides, against a mongomock database.
"""

from typing import Any

import httpx
import pytest

from app.chat.config import chat_settings
from app.chat.models import ChatThreadDocument

pytestmark = pytest.mark.usefixtures("_override_deps")

# ---------------------------------------------------------------------------
# GET /chat/{chat_id}
# ---------------------------------------------------------------------------


async def test_get_chat_messages_applies_default_page_size(
    mock_client: httpx.AsyncClient,
    persisted_thread_doc: ChatThreadDocument,
    persist_messages: Any,
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
    mock_client: httpx.AsyncClient,
    persisted_thread_doc: ChatThreadDocument,
    persist_messages: Any,
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
    mock_client: httpx.AsyncClient,
    persisted_thread_doc: ChatThreadDocument,
    persist_messages: Any,
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
    mock_client: httpx.AsyncClient,
    persisted_thread_doc: ChatThreadDocument,
    persist_messages: Any,
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
    mock_client: httpx.AsyncClient,
    persisted_thread_doc: ChatThreadDocument,
) -> None:
    """A `limit` below 1 is rejected."""
    resp = await mock_client.get(
        f"/chat/{persisted_thread_doc.id}", params={"limit": 0}
    )

    assert resp.status_code == 422


async def test_get_chat_messages_rejects_unknown_cursor(
    mock_client: httpx.AsyncClient,
    persisted_thread_doc: ChatThreadDocument,
    persist_messages: Any,
) -> None:
    """A cursor that is not a message of this thread is rejected."""
    await persist_messages(persisted_thread_doc.id, 3)

    resp = await mock_client.get(
        f"/chat/{persisted_thread_doc.id}",
        params={"before": "000000000000000000000099"},
    )

    assert resp.status_code == 422
