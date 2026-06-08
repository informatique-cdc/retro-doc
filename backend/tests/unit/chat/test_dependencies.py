"""Unit tests for chat dependencies.

This module tests the chat dependencies.
"""

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from app.auth.schemas import User
from app.chat.dependencies import get_verified_chat_thread
from app.chat.models import ChatThreadDocument

# ---------------------------------------------------------------------------
# get_verified_chat_thread
# ---------------------------------------------------------------------------


async def test_get_verified_chat_thread_raises_404_for_other_user(
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
    user: User,
) -> None:
    """Raises HTTP 404 when the thread does not exist."""
    fake_id = PydanticObjectId("000000000000000000000099")

    with pytest.raises(HTTPException) as exc_info:
        await get_verified_chat_thread(fake_id, user)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Chat thread not found."


async def test_get_verified_chat_thread_returns_when_exists(
    user: User,
    persisted_thread_doc: ChatThreadDocument,
) -> None:
    """Returns the thread when it exists and belongs to the user."""
    result = await get_verified_chat_thread(
        persisted_thread_doc.id,  # type: ignore[arg-type]
        user,
    )

    assert result.id == persisted_thread_doc.id
