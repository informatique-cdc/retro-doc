"""Repository FastAPI dependencies.

This module defines reusable FastAPI dependencies for repository
document lookups with ownership verification.
"""

from typing import Annotated

from beanie import PydanticObjectId
from fastapi import Depends, Form, HTTPException, status

from app.languages.service import get_supported_languages
from app.repos.models import FileDocument, RepoDocument
from app.users.dependencies import VerifiedUserRepo


async def get_validated_languages(
    languages: list[str] = Form(
        default_factory=list,
        description="Languages to analyze (e.g. python, java). "
        "Empty means all supported languages.",
    ),
) -> list[str]:
    """Validate the requested language filter against the worker's supported set.

    An empty list means "analyze all supported languages" and is always valid
    (no worker call needed). Otherwise every value must be supported; on a miss
    the cached list is refreshed once before rejecting, to tolerate a language
    the worker added since the cache was last filled.

    Args:
        languages(list[str]): The languages to analyze (multipart form field).

    Returns:
        list[str]: The validated languages.

    Raises:
        HTTPException: 422 if any language is not supported by the worker.
    """
    if not languages:
        return []

    supported = set(await get_supported_languages())
    invalid = [language for language in languages if language not in supported]
    if invalid:
        # Cache may be stale (missing a newly-added language); refresh once.
        supported = set(await get_supported_languages(force=True))
        invalid = [language for language in languages if language not in supported]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported language(s): {invalid}",
        )

    return languages


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
ValidatedLanguages = Annotated[list[str], Depends(get_validated_languages)]
