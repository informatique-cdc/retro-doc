"""Repository analysis service.

This module contains the business logic for repository analysis,
separated from the HTTP/routing concerns in router.py.
"""

import re
from typing import BinaryIO
from uuid import uuid4

from beanie import PydanticObjectId
from beanie.operators import GT, In, Inc
from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from app.auth.schemas import User
from app.chat.service import delete_threads_by_repo
from app.core.blob_storage import get_container_client
from app.core.language_enum import Language
from app.deep_analysis.service import delete_analyses_by_repo
from app.docs.models import FileDocumentationDocument, MetaRepoDocument
from app.graphs.models import ASTDocument, CFGDocument, DFGDocument
from app.pipeline.models import PipelineRunDocument
from app.pipeline.service import start_orchestration
from app.repos.models import FileDocument, RepoDocument
from app.users.models import UserRepoDocument


async def analyze_file(
    filename: str,
    file_data: BinaryIO,
    name: str,
    language: Language,
    user: User,
    color: str | None = None,
) -> PydanticObjectId:
    """Validate, upload, and start analysis of a ZIP file.

    Args:
        filename(str): The original filename of the uploaded file.
        file_data(BinaryIO): The file-like object containing the ZIP data.
        name(str): The display name for the repository.
        language(Language): The programming language to analyze.
        user(User): The authenticated user submitting the file.
        color(str | None): Optional hex color for the repository.

    Returns:
        PydanticObjectId: The newly created RepoDocument ID as a string.

    Raises:
        HTTPException: 400 if the file is not a ZIP file.
    """
    repo_name = filename
    if not repo_name or not repo_name.endswith(".zip"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .zip files are accepted.",
        )

    # Generate a unique blob name and upload the file to Azure Blob Storage
    blob_name = f"user/{user.uid}/{uuid4()}/src/{repo_name}"

    container_client = get_container_client()
    await container_client.upload_blob(name=blob_name, data=file_data, overwrite=True)

    # Create RepoDocument, UserRepoDocument, and PipelineRunDocument in MongoDB
    repo = RepoDocument(
        repo_url=repo_name,  # Store the original filename as repo_url for reference
        blob_path=blob_name.removesuffix(".zip"),
        language=language.value,
    )
    await repo.insert()

    user_repo = UserRepoDocument(
        user_id=user.uid,
        repo_id=repo.id,
        name=name,
        color=color,
    )
    await user_repo.insert()

    pipeline_run = PipelineRunDocument(
        repo_id=repo.id,
    )
    await pipeline_run.insert()

    # Start the analysis orchestration asynchronously
    await start_orchestration(blob_name, language, pipeline_run)

    return repo.id  # type: ignore


async def delete_repo(user_repo: UserRepoDocument, repo: RepoDocument) -> None:
    """Unlink a repository from the user and decrement its user count.

    Deletes children (threads, analyses) before the parent link to ensure
    partial failures leave the system in a retryable state. If the user
    count reaches zero, the repository is considered garbage and should be
    cleaned up by a separate job. Orphaned messages and checkpointer
    data should also be handled by a separate job.

    Args:
        user_repo(UserRepoDocument): The user-repo link.
        repo(RepoDocument): The repository document.
    """
    await delete_threads_by_repo(user_repo.user_id, user_repo.repo_id)
    await delete_analyses_by_repo(user_repo.user_id, user_repo.repo_id)
    await user_repo.delete()
    await repo.update(Inc({RepoDocument.user_count: -1}))  # type: ignore[no-untyped-call]


async def get_pipeline(repo_id: PydanticObjectId) -> PipelineRunDocument:
    """Retrieve the latest pipeline run for a repository.

    Args:
        repo_id(PydanticObjectId): The RepoDocument ID.

    Returns:
        PipelineRunDocument: The most recent PipelineRunDocument for the repository.

    Raises:
        HTTPException: 404 if no pipeline run exists.
    """
    pipeline_run = await PipelineRunDocument.find_one(
        PipelineRunDocument.repo_id == repo_id,
        sort=[("_id", -1)],
    )
    if pipeline_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pipeline run found for this repository.",
        )
    return pipeline_run


async def get_file_documentation(
    repo_id: PydanticObjectId, file_id: PydanticObjectId
) -> FileDocumentationDocument:
    """Retrieve the documentation for a specific file in a repository.

    Args:
        repo_id(PydanticObjectId): The RepoDocument ID.
        file_id(PydanticObjectId): The FileDocument ID.

    Returns:
        FileDocumentationDocument: The documentation for the file.

    Raises:
        HTTPException: 404 if the documentation is not found.
    """
    documentation = await FileDocumentationDocument.find_one(
        FileDocumentationDocument.repo_id == repo_id,
        FileDocumentationDocument.file_id == file_id,
    )
    if documentation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No documentation found for this file.",
        )
    return documentation


async def get_file_graphs(
    repo_id: PydanticObjectId, file_id: PydanticObjectId
) -> tuple[ASTDocument | None, list[CFGDocument], list[DFGDocument]]:
    """Retrieve the AST, CFG, and DFG graphs for a specific file.

    Args:
        repo_id(PydanticObjectId): The RepoDocument ID.
        file_id(PydanticObjectId): The FileDocument ID.

    Returns:
        tuple[ASTDocument | None, list[CFGDocument], list[DFGDocument]]:
            The AST graph (or None), and lists of CFG and DFG graphs (one per scope).
    """
    ast = await ASTDocument.find_one(
        ASTDocument.repo_id == repo_id,
        ASTDocument.file_id == file_id,
    )
    cfgs = await CFGDocument.find(
        CFGDocument.repo_id == repo_id,
        CFGDocument.file_id == file_id,
    ).to_list()
    dfgs = await DFGDocument.find(
        DFGDocument.repo_id == repo_id,
        DFGDocument.file_id == file_id,
    ).to_list()

    return ast, cfgs, dfgs


async def get_file_source(repo: RepoDocument, file_doc: FileDocument) -> str:
    """Retrieve the source content of a file from blob storage.

    Args:
        repo(RepoDocument): The verified repository document.
        file_doc(FileDocument): The verified file document.

    Returns:
        str: The file content.
    """
    blob_path = f"{repo.blob_path}/{file_doc.path}"
    container_client = get_container_client()
    downloader = await container_client.download_blob(blob_path)
    raw = await downloader.readall()
    return raw.decode(errors="replace")


async def get_files(repo_id: PydanticObjectId) -> list[FileDocument]:
    """Retrieve all files belonging to a repository.

    Args:
        repo_id(PydanticObjectId): The RepoDocument ID.

    Returns:
        list[FileDocument]: All FileDocuments associated with the repository.
    """
    files = await FileDocument.find(
        FileDocument.repo_id == repo_id,
    ).to_list()
    return files


async def get_repo_meta(
    repo_id: PydanticObjectId,
) -> MetaRepoDocument | None:
    """Retrieve the meta document for a repository.

    Args:
        repo_id(PydanticObjectId): The RepoDocument ID.

    Returns:
        MetaRepoDocument | None: The meta document, or None if it doesn't exist.
    """
    return await MetaRepoDocument.find_one(
        MetaRepoDocument.repo_id == PydanticObjectId(repo_id),
    )


async def get_repos(
    user: User, search: str | None = None
) -> list[tuple[RepoDocument, UserRepoDocument]]:
    """Retrieve all repositories belonging to the user.

    Args:
        user(User): The authenticated user.
        search(str | None): Optional search string for case-insensitive
            substring match on the repository display name.

    Returns:
        list[tuple[RepoDocument, UserRepoDocument]]: A list of
            (RepoDocument, UserRepoDocument) tuples.
    """
    query = UserRepoDocument.find(UserRepoDocument.user_id == user.uid)
    if search is not None:
        query = query.find({"name": {"$regex": re.escape(search), "$options": "i"}})
    user_repos = await query.sort("-_id").to_list()

    if not user_repos:
        return []

    repo_ids = [ur.repo_id for ur in user_repos]
    repos = await RepoDocument.find(In(RepoDocument.id, repo_ids)).to_list()
    repo_map = {repo.id: repo for repo in repos}
    return [(repo_map[ur.repo_id], ur) for ur in user_repos]


async def join_repo(repo_id: PydanticObjectId, user: User) -> str:
    """Join an existing repository, adding it to the current user's list.

    Atomically increments the repository's user_count (only if > 0, i.e. not
    garbage-collected) and creates a UserRepoDocument linking the user to the
    repository. The display name is copied from an existing user-repo link.

    Args:
        repo_id(PydanticObjectId): The RepoDocument ID to join.
        user(User): The authenticated user.

    Returns:
        str: The copied display name.

    Raises:
        HTTPException: 409 if the user already has this repository.
        HTTPException: 404 if the repository does not exist or is
            no longer available (user_count <= 0).
    """
    # Copy the display name from any existing user-repo link
    source_user_repo = await UserRepoDocument.find_one(
        UserRepoDocument.repo_id == repo_id,
    )
    if source_user_repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )
    name = source_user_repo.name

    # Create the user-repo link
    user_repo = UserRepoDocument(user_id=user.uid, repo_id=repo_id, name=name)
    try:
        await user_repo.insert()
    except DuplicateKeyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repository already added to your account.",
        )

    # Atomically increment user_count only if > 0 (not garbage-collected)
    inc_result = await RepoDocument.find_one(
        RepoDocument.id == repo_id,
        GT(RepoDocument.user_count, 0),
    ).update(Inc({RepoDocument.user_count: 1}))  # type: ignore[no-untyped-call]

    if inc_result.modified_count == 0:
        # Repo gone or garbage-collected — rollback the user-repo link (BASE principle)
        await user_repo.delete()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found or no longer available.",
        )

    return name


async def update_user_repo(
    user_repo: UserRepoDocument,
    color: str | None,
    name: str | None,
    fields_set: set[str],
) -> UserRepoDocument:
    """Update per-user metadata for a repository.

    Only fields present in *fields_set* are applied. Omitted fields are left
    unchanged; `color` sent as `null` clears the value, while `name`
    sent as `null` is ignored (name is required).

    Args:
        user_repo(UserRepoDocument): The user-repo link to update.
        color(str | None): The hex color string, or None to clear.
        name(str | None): The display name, or None to leave unchanged.
        fields_set(set[str]): Fields explicitly provided by the client
            (from `UpdateUserRepoRequest.model_fields_set`).

    Returns:
        UserRepoDocument: The updated user-repo document.
    """
    if "name" in fields_set and name is not None:
        user_repo.name = name
    if "color" in fields_set:
        user_repo.color = color
    await user_repo.save()
    return user_repo
