"""Repos service.

This module defines the service layer for repository-related operations.
"""

from datetime import UTC, datetime

from beanie import PydanticObjectId
from loguru import logger
from pymongo.errors import DuplicateKeyError

from app.core.database import mongodb_retry
from app.repos.models import FileDocument, RepoDocument


async def persist_file(
    repo_id: PydanticObjectId, relative_path: str
) -> PydanticObjectId:
    """Persist a file document for the given repository and relative path.

    Args:
        repo_id (PydanticObjectId): The repository ID.
        relative_path (str): The relative path of the file within the repository.

    Returns:
        PydanticObjectId: The ID of the persisted file document.
    """
    file_doc = FileDocument(repo_id=repo_id, path=relative_path)
    try:
        await mongodb_retry(file_doc.insert)
    except DuplicateKeyError:
        file_doc = await FileDocument.find_one(  # type: ignore
            FileDocument.repo_id == repo_id,
            FileDocument.path == relative_path,
        )
    return file_doc.id  # type: ignore


async def persist_repo(
    repo_id: PydanticObjectId, update_fields: dict[str, object]
) -> RepoDocument | None:
    """Persist updates to the RepoDocument.

    Args:
        repo_id (PydanticObjectId): The ID of the RepoDocument to update.
        update_fields (dict[str, object]): The fields to set on the document.

    Returns:
        RepoDocument | None: The updated document, or None if it was not found.
    """
    return await RepoDocument.find_one(  # type: ignore
        RepoDocument.id == repo_id
    ).update({"$set": update_fields})


async def stamp_ran_analyzer_version(
    repo_id: PydanticObjectId, analyzer_version: str
) -> None:
    """Record which analyzer version actually produces this repository's artifacts.

    Args:
        repo_id (PydanticObjectId): The ID of the RepoDocument to stamp.
        analyzer_version (str): The analyzer version this worker runs.

    Raises:
        ValueError: If the RepoDocument with the given ID is not found.
    """
    result = await mongodb_retry(
        persist_repo,
        repo_id,
        {
            "ran_analyzer_version": analyzer_version,
            "updated_at": datetime.now(UTC),
        },
    )
    if result is None:
        raise ValueError(f"RepoDocument '{repo_id}' not found")

    logger.info(f"Repos: '{repo_id}' analyzed by version {analyzer_version}")
