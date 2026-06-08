"""Repository FastAPI dependencies.

This module defines reusable FastAPI dependencies for repository
document lookups with ownership verification.
"""

from typing import Annotated

from beanie import PydanticObjectId
from fastapi import Depends, HTTPException, status

from app.repos.models import FileDocument, RepoDocument
from app.users.dependencies import VerifiedUserRepo


async def get_verified_repo(
    repo_id: PydanticObjectId,
    _user_repo: VerifiedUserRepo,
) -> RepoDocument:
    """Fetch a RepoDocument after verifying user ownership.

    Args:
        repo_id(PydanticObjectId): The repository ID from the path parameter.
        _user_repo(VerifiedUserRepo): The ownership link (injected by FastAPI).

    Returns:
        RepoDocument: The repository document.

    Raises:
        HTTPException: 404 if the repository document does not exist.
    """
    repo = await RepoDocument.get(repo_id)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )
    return repo


async def get_verified_file(
    file_id: PydanticObjectId,
    repo_id: PydanticObjectId,
    _user_repo: VerifiedUserRepo,
) -> FileDocument:
    """Fetch a FileDocument after verifying user ownership of the parent repo.

    Args:
        file_id(PydanticObjectId): The file ID from the path parameter.
        repo_id(PydanticObjectId): The repository ID from the path parameter.
        _user_repo(VerifiedUserRepo): The ownership link (injected by FastAPI).

    Returns:
        FileDocument: The file document.

    Raises:
        HTTPException: 404 if the file does not exist in this repository.
    """
    file_doc = await FileDocument.find_one(
        FileDocument.id == file_id,
        FileDocument.repo_id == repo_id,
    )
    if file_doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found in this repository.",
        )
    return file_doc


VerifiedRepo = Annotated[RepoDocument, Depends(get_verified_repo)]
VerifiedFile = Annotated[FileDocument, Depends(get_verified_file)]
