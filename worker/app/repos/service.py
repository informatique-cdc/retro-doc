"""Repos service.

This module defines the service layer for repository-related operations.
"""

from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError

from app.core.database import mongodb_retry
from app.repos.models import FileDocument


async def persist_file(
    repo_id: PydanticObjectId, relative_path: str, file_hash: str
) -> PydanticObjectId:
    """Persist a file document for the given repository and relative path.

    Args:
        repo_id (PydanticObjectId): The repository ID.
        relative_path (str): The relative path of the file within the repository.
        file_hash (str): The hash of the file content.

    Returns:
        PydanticObjectId: The ID of the persisted file document.
    """
    file_doc = FileDocument(repo_id=repo_id, path=relative_path, file_hash=file_hash)
    try:
        await mongodb_retry(file_doc.insert)
    except DuplicateKeyError:
        file_doc = await FileDocument.find_one(  # type: ignore
            FileDocument.repo_id == repo_id,
            FileDocument.path == relative_path,
        )
    return file_doc.id  # type: ignore
