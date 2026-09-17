"""Chat FastAPI dependencies.

This module defines reusable FastAPI dependencies for chat-related
access verification and resource resolution.
"""

from typing import Annotated

from beanie import PydanticObjectId
from fastapi import Depends, HTTPException, status

from app.auth.dependencies import CurrentUser
from app.chat.models import ChatMessageDocument, ChatThreadDocument
from app.chat.schemas import CreateChatRequest, RetryMessageRequest
from app.users.dependencies import get_verified_user_repo


async def verify_chat_repo_access(
    request: CreateChatRequest,
    user: CurrentUser,
) -> None:
    """Verify the authenticated user has access to the requested repository.

    Args:
        request(CreateChatRequest): The chat creation request containing `repo_id`.
        user(CurrentUser): The authenticated user (injected by FastAPI).
    """
    await get_verified_user_repo(request.repo_id, user)


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


async def get_verified_retry_target(
    request: RetryMessageRequest,
    thread: "VerifiedChatThread",
) -> tuple[ChatMessageDocument, str]:
    """Check that an answer can be regenerated, and find the question it answers.

    This ensures that the answer being regenerated is on the selected branch
    and that the necessary checkpoint information is available. It need not be
    the last answer of that branch: regenerating one that was already followed
    up takes those follow-ups out of the conversation along with it, and
    selecting the old answer again brings them back.

    An answer on an abandoned branch is refused rather than silently switched
    to, so regenerating always acts on the conversation as the caller last saw
    it. Selecting that branch first, with `POST /chat/{chat_id}/variant`, makes
    it retryable.

    Args:
        request(RetryMessageRequest): The request containing the ID of the
            answer to regenerate.
        thread(VerifiedChatThread): The verified chat thread document
            (injected by FastAPI).

    Returns:
        tuple[ChatMessageDocument, str]: The answer, and the content of the
            user message that prompted it.

    Raises:
        HTTPException: 404 if the ID doesn't identify an answer in this
            thread, 409 if that answer is not on the selected branch or
            predates the checkpoint tracking this relies on.
    """
    target = await ChatMessageDocument.find_one(
        ChatMessageDocument.id == request.message_id,
        ChatMessageDocument.thread_id == thread.id,
    )
    if target is None or target.role != "ai":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found.",
        )

    if not target.active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only an answer on the current conversation can be regenerated.",
        )

    if target.parent_checkpoint_id is None and target.input_checkpoint_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This answer cannot be regenerated.",
        )

    prompt = await ChatMessageDocument.find_one(
        ChatMessageDocument.thread_id == thread.id,
        {"role": "human", "parent_checkpoint_id": target.parent_checkpoint_id},
    )
    if prompt is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This answer cannot be regenerated.",
        )

    return target, prompt.content


ChatRepoAccess = Annotated[None, Depends(verify_chat_repo_access)]
VerifiedChatThread = Annotated[ChatThreadDocument, Depends(get_verified_chat_thread)]
VerifiedRetryTarget = Annotated[
    tuple[ChatMessageDocument, str], Depends(get_verified_retry_target)
]
