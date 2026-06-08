"""Users FastAPI dependencies.

This module defines reusable FastAPI dependencies for user-repository
ownership verification.
"""

from typing import Annotated

from beanie import PydanticObjectId
from fastapi import Depends, HTTPException, status

from app.auth.dependencies import CurrentUser
from app.users.models import UserRepoDocument


async def get_user_repo(
    repo_id: PydanticObjectId,
    user: CurrentUser,
) -> UserRepoDocument:
    """Verify the authenticated user owns the given repository.

    Args:
        repo_id(PydanticObjectId): The repository ID from the path parameter.
        user(User): The authenticated user (injected by FastAPI).

    Returns:
        UserRepoDocument: The user-repo link document.

    Raises:
        HTTPException: 404 if the user does not have access to this repository.
    """
    user_repo = await UserRepoDocument.find_one(
        UserRepoDocument.user_id == user.uid,
        UserRepoDocument.repo_id == repo_id,
    )
    if user_repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )
    return user_repo


VerifiedUserRepo = Annotated[UserRepoDocument, Depends(get_user_repo)]
