"""Repository router.

This module defines the API endpoints related to repository.
"""

from beanie import PydanticObjectId
from fastapi import APIRouter, Form, Response, UploadFile, status

from app.auth.dependencies import CurrentUser
from app.pipeline.service import get_cached_analyzer_version
from app.repos.dependencies import VerifiedFile, VerifiedRepo
from app.repos.schemas import (
    CreateRepoResponse,
    FileDocumentationResponse,
    FileGraphsResponse,
    FileResponse,
    FileSourceResponse,
    JoinRepoResponse,
    PipelineAttempt,
    PipelineStatusResponse,
    RelaunchRepoRequest,
    RelaunchRepoResponse,
    RepoDetailResponse,
    RepoFilesResponse,
    RepoListResponse,
    RepoResponse,
    ScopedGraph,
    UpdateUserRepoRequest,
)
from app.repos.service import (
    create_repo,
    delete_repo,
    get_file_documentation,
    get_file_graphs,
    get_file_source,
    get_files,
    get_pipeline_with_attempts,
    get_repo_meta,
    get_repos,
    is_stale,
    join_repo,
    relaunch_repo,
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
    current_version = await get_cached_analyzer_version()

    return RepoListResponse(
        repos=[
            RepoResponse(
                repo_id=repo.id,  # type: ignore
                name=user_repo.name,
                repo_url=repo.repo_url,
                repo_hash=repo.repo_hash,
                languages=repo.languages,
                analyzer_version=repo.ran_analyzer_version,
                stale=is_stale(repo, current_version),
                color=user_repo.color,
                created_at=repo.created_at,
                updated_at=repo.updated_at,
            )
            for repo, user_repo in repos
        ]
    )


@repos_router.post(
    "",
    response_model=CreateRepoResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_repo_endpoint(
    response: Response,
    user: CurrentUser,
    file: UploadFile | None = None,
    languages: list[str] = Form(
        default_factory=list,
        description="Languages to analyze (e.g. python, java). "
        "Empty means all supported languages.",
    ),
    repo_url: str | None = Form(
        default=None,
        description="A git repository URL to analyze.",
    ),
    branch: str | None = Form(
        default=None,
        description="Optional git branch to resolve the commit from.",
    ),
    commit: str | None = Form(
        default=None,
        description="Optional git commit SHA to analyze.",
    ),
    token: str | None = Form(
        default=None,
        description="Optional personal or project access token for a private git repository.",
    ),
    name: str = Form(
        description="A custom display name for the repository.",
    ),
    color: str | None = Form(
        default=None,
        max_length=50,
        description="Optional color for the repository (e.g. #FF5733).",
    ),
) -> CreateRepoResponse:
    """Create a new repository from a zip file upload or a git URL.

    Exactly one of `file` (zip upload) or `repo_url` (git) must be provided.
    Zip uploads honor the `languages` filter and are personal. Git analyses are
    shared (deduplicated by commit + analyzer version), always analyze all
    languages, and `join` an existing analysis when one already exists (200
    instead of 202).

    Joining an analysis that had failed restarts it, so the response is 200
    with `status: "pending"` rather than the earlier failure: a create asks for
    an analysis, and a failed run is not one. The status code answers "a new
    repository, or one that already existed?". The body's `status` answers
    "what is happening to it?".

    Args:
        response(Response): The response, used to switch to 200 on a git join
            (injected by FastAPI).
        user(CurrentUser): The authenticated user (injected by FastAPI).
        file(UploadFile | None): The zip file containing the source code.
        languages(list[str]): The languages to analyze (zip only;
            empty = all supported).
        repo_url(str | None): A git repository URL (alternative to `file`).
        branch(str | None): Optional git branch to resolve the commit from.
        commit(str | None): Optional git commit SHA to analyze.
        token(str | None): Optional personal or project access token used
            to reach a private git repository.
        name(str): A custom display name for the repository.
        color(str | None): Optional hex color for the repository.

    Returns:
        CreateRepoResponse: Contains repo_id and status.
    """
    repo_id, repo_status, joined = await create_repo(
        filename=file.filename if file is not None else None,
        file_data=file.file if file is not None else None,
        repo_url=repo_url,
        branch=branch,
        commit=commit,
        token=token,
        name=name,
        languages=languages,
        user=user,
        color=color,
    )
    if joined:
        response.status_code = status.HTTP_200_OK
    return CreateRepoResponse(repo_id=repo_id, status=repo_status)


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
        JoinRepoResponse: The repo ID and the display name taken from `repo_url`.
    """
    name = await join_repo(repo_id, user)

    return JoinRepoResponse(repo_id=repo_id, name=name)


@repos_router.post(
    "/{repo_id}/relaunch",
    response_model=RelaunchRepoResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def relaunch_repo_endpoint(
    user: CurrentUser,
    user_repo: VerifiedUserRepo,
    repo: VerifiedRepo,
    request: RelaunchRepoRequest | None = None,
) -> RelaunchRepoResponse:
    """Re-analyze a repository with the worker's current analyzer version.

    This is also how a failed analysis is retried: there is no separate retry
    endpoint. The response's `repo_id` tells the two apart — a new repository at
    a newer version, the caller's own when a failed run was retried in place.

    Nothing is replaced. The existing repository stays listed with its
    documentation, graphs, chat threads and deep analyses. A retry adds a new
    entry to `attempts`, leaving the failed one marked `retried_at`. A relaunch
    with nothing to do is refused with 409: a completed analysis at the current
    version is what was asked for, and a pending or running one is already
    producing it.

    A relaunch already made is refused the same way. The second call finds the
    analysis the first one produced, which the caller is by then a holder of,
    and answers 409 rather than adding a second entry for identical work. That
    is as true of an uploaded archive as of a git commit — the upload is what
    identifies it, not the filename it was sent under.

    A git repository must be reachable, checked before anything is written, and
    with the supplied token when it is private: relaunching is what makes the
    analysis fetch the remote again, so it takes access to the remote, unlike
    joining an analysis that already exists. A public remote is checked too, so
    one deleted or since made private is refused here rather than dispatched.

    Args:
        user(CurrentUser): The authenticated user (injected by FastAPI).
        user_repo(VerifiedUserRepo): The verified user-repo link, supplying the
            display name and color to carry over (injected by FastAPI).
        repo(VerifiedRepo): The verified repository document (injected by
            FastAPI).
        request(RelaunchRepoRequest | None): Optional body carrying an access
            token for a private git remote.

    Returns:
        RelaunchRepoResponse: The resulting repository ID — a new one, or the
            caller's own when a failed run is retried in place — and its
            pipeline status.
    """
    repo_id, repo_status = await relaunch_repo(
        repo, user_repo, user, request.token if request else None
    )

    return RelaunchRepoResponse(repo_id=repo_id, status=repo_status)


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
    current_version = await get_cached_analyzer_version()

    return RepoDetailResponse(
        repo_id=repo.id,  # type: ignore
        name=user_repo.name,
        repo_url=repo.repo_url,
        repo_hash=repo.repo_hash,
        languages=repo.languages,
        analyzer_version=repo.ran_analyzer_version,
        stale=is_stale(repo, current_version),
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
    current_version = await get_cached_analyzer_version()

    return RepoResponse(
        repo_id=repo.id,  # type: ignore
        name=updated.name,
        repo_url=repo.repo_url,
        repo_hash=repo.repo_hash,
        languages=repo.languages,
        analyzer_version=repo.ran_analyzer_version,
        stale=is_stale(repo, current_version),
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
    """Get the status of a repository's pipeline run, and of the attempts before it.

    `attempts` is ordered newest first, so `attempts[0]` is the attempt the
    top-level `status` and `meta` describe.

    Args:
        repo_id(PydanticObjectId): The repository ID.
        _user_repo(VerifiedUserRepo): The verified user-repo link
            (injected by FastAPI).

    Returns:
        PipelineStatusResponse: Contains repo_id, the latest run's status and
            optional meta information, and the non-empty list of attempts,
            newest first.
    """
    current, attempts = await get_pipeline_with_attempts(repo_id)

    return PipelineStatusResponse(
        repo_id=repo_id,
        status=current.status,
        meta=current.meta,
        attempts=[
            PipelineAttempt(
                status=run.status,
                started_at=run.started_at,
                finished_at=run.finished_at,
                retried_at=run.retried_at,
                meta=run.meta,
            )
            for run in attempts
        ],
    )
