"""Repository router.

This module defines the API endpoints related to repository.
"""

from beanie import PydanticObjectId
from fastapi import APIRouter, Form, HTTPException, UploadFile, status

from app.auth.dependencies import CurrentUser
from app.repos.dependencies import ValidatedLanguages, VerifiedFile, VerifiedRepo
from app.repos.schemas import (
    AnalyzeFileResponse,
    FileDocumentationResponse,
    FileGraphsResponse,
    FileResponse,
    FileSourceResponse,
    JoinRepoResponse,
    PipelineStatusResponse,
    RepoDetailResponse,
    RepoFilesResponse,
    RepoListResponse,
    RepoResponse,
    ScopedGraph,
    UpdateUserRepoRequest,
)
from app.repos.service import (
    analyze_file,
    delete_repo,
    get_file_documentation,
    get_file_graphs,
    get_file_source,
    get_files,
    get_pipeline,
    get_repo_meta,
    get_repos,
    join_repo,
    update_user_repo,
)
from app.users.dependencies import VerifiedUserRepo

repos_router = APIRouter(prefix="/repos", tags=["repos"])


@repos_router.get("", response_model=RepoListResponse)
async def get_repos_endpoint(
    user: CurrentUser,
    search: str | None = None,
) -> RepoListResponse:
    """Get all repositories belonging to the authenticated user.

    Args:
        user(CurrentUser): The authenticated user (injected by FastAPI).
        search(str | None): Optional search string to filter repos by name.

    Returns:
        RepoListResponse: A list of the user's repositories.
    """
    repos = await get_repos(user, search)

    return RepoListResponse(
        repos=[
            RepoResponse(
                repo_id=repo.id,  # type: ignore
                name=user_repo.name,
                repo_url=repo.repo_url,
                repo_branch=repo.repo_branch,
                repo_hash=repo.repo_hash,
                languages=repo.languages,
                color=user_repo.color,
                created_at=repo.created_at,
                updated_at=repo.updated_at,
            )
            for repo, user_repo in repos
        ]
    )


@repos_router.post(
    "",
    response_model=AnalyzeFileResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_repo_endpoint(
    file: UploadFile,
    user: CurrentUser,
    languages: ValidatedLanguages,
    name: str = Form(description="A custom display name for the repository."),
    color: str | None = Form(
        default=None,
        max_length=50,
        description="Optional color for the repository (e.g. #FF5733).",
    ),
) -> AnalyzeFileResponse:
    """Create a new repository from a zip file upload.

    Args:
        file(UploadFile): The zip file containing the source code.
        user(CurrentUser): The authenticated user (injected by FastAPI).
        languages(ValidatedLanguages): The languages to analyze, validated
            against the worker's supported set (empty = all supported).
        name(str): A custom display name for the repository.
        color(str | None): Optional hex color for the repository.

    Returns:
        AnalyzeFileResponse: Contains repo_id and status.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    repo_id = await analyze_file(file.filename, file.file, name, languages, user, color)

    return AnalyzeFileResponse(repo_id=repo_id)


@repos_router.post(
    "/{repo_id}/join",
    response_model=JoinRepoResponse,
    status_code=status.HTTP_201_CREATED,
)
async def join_repo_endpoint(
    repo_id: PydanticObjectId,
    user: CurrentUser,
) -> JoinRepoResponse:
    """Join an existing repository, adding it to the authenticated user's list.

    Args:
        repo_id(PydanticObjectId): The repository ID to join.
        user(CurrentUser): The authenticated user (injected by FastAPI).

    Returns:
        JoinRepoResponse: The repo ID and copied display name.
    """
    name = await join_repo(repo_id, user)

    return JoinRepoResponse(repo_id=repo_id, name=name)


@repos_router.get("/{repo_id}", response_model=RepoDetailResponse)
async def get_repo_endpoint(
    user_repo: VerifiedUserRepo,
    repo: VerifiedRepo,
) -> RepoDetailResponse:
    """Get a single repository's details and meta content.

    Args:
        user_repo(VerifiedUserRepo): The verified user-repo link (injected
            by FastAPI).
        repo(VerifiedRepo): The verified repository document (injected by
            FastAPI).

    Returns:
        RepoDetailResponse: The repository details with meta content.
    """
    meta = await get_repo_meta(repo.id)  # type: ignore

    return RepoDetailResponse(
        repo_id=repo.id,  # type: ignore
        name=user_repo.name,
        repo_url=repo.repo_url,
        repo_branch=repo.repo_branch,
        repo_hash=repo.repo_hash,
        languages=repo.languages,
        color=user_repo.color,
        created_at=repo.created_at,
        updated_at=repo.updated_at,
        content=meta.content if meta else None,
        stats=meta.stats if meta else None,
    )


@repos_router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_repo_endpoint(
    user_repo: VerifiedUserRepo,
    repo: VerifiedRepo,
) -> None:
    """Remove a repository from the authenticated user's list.

    Args:
        user_repo(VerifiedUserRepo): The verified user-repo link
            (injected by FastAPI).
        repo(VerifiedRepo): The verified repository document (injected
            by FastAPI).
    """
    await delete_repo(user_repo, repo)


@repos_router.patch("/{repo_id}", response_model=RepoResponse)
async def update_user_repo_endpoint(
    request: UpdateUserRepoRequest,
    user_repo: VerifiedUserRepo,
    repo: VerifiedRepo,
) -> RepoResponse:
    """Update the user-specific metadata for a repository.

    Args:
        request(UpdateUserRepoRequest): The request containing fields to update.
        user_repo(VerifiedUserRepo): The verified user-repo link (injected
            by FastAPI).
        repo(VerifiedRepo): The verified repository document (injected by
            FastAPI).

    Returns:
        RepoResponse: The updated repository.
    """
    updated = await update_user_repo(
        user_repo, request.color, request.name, request.model_fields_set
    )

    return RepoResponse(
        repo_id=repo.id,  # type: ignore
        name=updated.name,
        repo_url=repo.repo_url,
        repo_branch=repo.repo_branch,
        repo_hash=repo.repo_hash,
        languages=repo.languages,
        color=updated.color,
        created_at=repo.created_at,
        updated_at=repo.updated_at,
    )


@repos_router.get(
    "/{repo_id}/files/{file_id}/doc", response_model=FileDocumentationResponse
)
async def get_file_documentation_endpoint(
    repo_id: PydanticObjectId,
    file_id: PydanticObjectId,
    _file_doc: VerifiedFile,
) -> FileDocumentationResponse:
    """Get the generated documentation for a specific file in a repository.

    Args:
        repo_id(PydanticObjectId): The repository ID.
        file_id(PydanticObjectId): The file ID.
        _file_doc(VerifiedFile): The verified file document (injected by
            FastAPI).

    Returns:
        FileDocumentationResponse: The file's documentation content.
    """
    documentation = await get_file_documentation(repo_id, file_id)

    return FileDocumentationResponse(
        repo_id=repo_id,
        file_id=file_id,
        content=documentation.content,
    )


@repos_router.get("/{repo_id}/files/{file_id}/src", response_model=FileSourceResponse)
async def get_file_source_endpoint(
    repo: VerifiedRepo,
    file_doc: VerifiedFile,
) -> FileSourceResponse:
    """Get the source content of a specific file in a repository.

    Args:
        repo(VerifiedRepo): The verified repository document (injected by
            FastAPI).
        file_doc(VerifiedFile): The verified file document (injected by
            FastAPI).

    Returns:
        FileSourceResponse: The file's source content.
    """
    content = await get_file_source(repo, file_doc)

    return FileSourceResponse(
        repo_id=repo.id,  # type: ignore
        file_id=file_doc.id,  # type: ignore
        path=file_doc.path,
        content=content,
    )


@repos_router.get(
    "/{repo_id}/files/{file_id}/graphs", response_model=FileGraphsResponse
)
async def get_file_graphs_endpoint(
    repo_id: PydanticObjectId,
    file_id: PydanticObjectId,
    _file_doc: VerifiedFile,
) -> FileGraphsResponse:
    """Get the AST, CFG, and DFG graphs for a specific file in a repository.

    Args:
        repo_id(PydanticObjectId): The repository ID.
        file_id(PydanticObjectId): The file ID.
        _file_doc(VerifiedFile): The verified file document (injected by
            FastAPI).

    Returns:
        FileGraphsResponse: The AST, CFG, and DFG graphs of the file.
    """
    ast, cfgs, dfgs = await get_file_graphs(repo_id, file_id)

    return FileGraphsResponse(
        repo_id=repo_id,
        file_id=file_id,
        ast=ast.content if ast else None,
        cfg=[ScopedGraph(scope=c.scope, content=c.content) for c in cfgs],
        dfg=[ScopedGraph(scope=d.scope, content=d.content) for d in dfgs],
    )


@repos_router.get("/{repo_id}/files", response_model=RepoFilesResponse)
async def get_files_endpoint(
    repo_id: PydanticObjectId,
    _user_repo: VerifiedUserRepo,
) -> RepoFilesResponse:
    """Get all files belonging to a repository.

    Args:
        repo_id(PydanticObjectId): The repository ID.
        _user_repo(VerifiedUserRepo): The verified user-repo link
            (injected by FastAPI).

    Returns:
        RepoFilesResponse: Contains repo_id and a list of files.
    """
    files = await get_files(repo_id)

    return RepoFilesResponse(
        repo_id=repo_id,
        files=[
            FileResponse(
                file_id=f.id,  # type: ignore
                path=f.path,
                file_hash=f.file_hash,
            )
            for f in files
        ],
    )


@repos_router.get(
    "/{repo_id}/pipeline",
    response_model=PipelineStatusResponse,
    response_model_exclude_none=True,
)
async def get_repo_pipeline_endpoint(
    repo_id: PydanticObjectId,
    _user_repo: VerifiedUserRepo,
) -> PipelineStatusResponse:
    """Get the status of a repository's pipeline run.

    Args:
        repo_id(PydanticObjectId): The repository ID.
        _user_repo(VerifiedUserRepo): The verified user-repo link
            (injected by FastAPI).

    Returns:
        PipelineStatusResponse: Contains repo_id, status and optional meta information.
    """
    pipeline_run = await get_pipeline(repo_id)

    return PipelineStatusResponse(
        repo_id=repo_id,
        status=pipeline_run.status,
        meta=pipeline_run.meta,
    )
