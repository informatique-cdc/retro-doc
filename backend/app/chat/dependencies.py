"""Chat FastAPI dependencies.

This module defines reusable FastAPI dependencies for chat-related
access verification and resource resolution.
"""

from typing import Annotated

from beanie import PydanticObjectId
from fastapi import Depends, HTTPException, status

from app.auth.dependencies import CurrentUser
from app.chat.models import ChatThreadDocument
from app.chat.schemas import CreateChatRequest
from app.users.dependencies import get_user_repo


async def verify_chat_repo_access(
    request: CreateChatRequest,
    user: CurrentUser,
) -> None:
    """Verify the authenticated user has access to the requested repository.

    Args:
        request(CreateChatRequest): The chat creation request containing `repo_id`.
        user(CurrentUser): The authenticated user (injected by FastAPI).
    """
    await get_user_repo(request.repo_id, user)


async def get_verified_chat_thread(
    chat_id: PydanticObjectId,
    user: CurrentUser,
) -> ChatThreadDocument:
    """Fetch a chat thread and verify the user owns it.

    Args:
        chat_id(PydanticObjectId): The thread's document ID from the path parameter.
        user(CurrentUser): The authenticated user (injected by FastAPI).

    Returns:
        ChatThreadDocument: The verified chat thread document.

    Raises:
        HTTPException: 404 if the thread does not exist or does not belong to the user.
    """
    thread = await ChatThreadDocument.find_one(
        ChatThreadDocument.id == chat_id,
        ChatThreadDocument.user_id == user.uid,
    )
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat thread not found.",
        )
    return thread


ChatRepoAccess = Annotated[None, Depends(verify_chat_repo_access)]
VerifiedChatThread = Annotated[ChatThreadDocument, Depends(get_verified_chat_thread)]
