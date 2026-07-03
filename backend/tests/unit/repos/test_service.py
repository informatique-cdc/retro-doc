"""Unit tests for repos service.

This module tests the repos service against a mongomock database,
with mocks used only where external dependencies or specific call
verification are needed.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from app.auth.schemas import User
from app.docs.models import AnalysisStats, FileDocumentationDocument, RepoMetaDocument
from app.graphs.models import ASTDocument, CFGDocument, DFGDocument
from app.pipeline.models import PipelineRunDocument
from app.repos.models import FileDocument, RepoDocument
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
from app.users.models import UserRepoDocument

# ---------------------------------------------------------------------------
# analyze_file
# ---------------------------------------------------------------------------


async def test_analyze_file_creates_all_documents(
    user: User,
    mock_blob_container: Any,
    mock_start_orchestration: Any,
) -> None:
    """Creates RepoDocument, UserRepoDocument, and PipelineRunDocument in the database."""
    repo_id = await analyze_file(
        "code.zip", MagicMock(), "my-repo", ["java"], user, color="#FF5733"
    )

    repo = await RepoDocument.get(repo_id)
    assert repo is not None
    assert repo.languages == ["java"]

    user_repos = await UserRepoDocument.find(
        UserRepoDocument.repo_id == repo_id
    ).to_list()
    assert len(user_repos) == 1
    assert user_repos[0].name == "my-repo"
    assert user_repos[0].color == "#FF5733"

    pipeline_runs = await PipelineRunDocument.find(
        PipelineRunDocument.repo_id == repo_id
    ).to_list()
    assert len(pipeline_runs) == 1

    mock_blob_container.upload_blob.assert_awaited_once()
    mock_start_orchestration.assert_awaited_once()


async def test_analyze_file_rejects_empty_filename(user: User) -> None:
    """Empty filename is rejected with HTTP 400."""
    with pytest.raises(HTTPException) as exc_info:
        await analyze_file("", MagicMock(), "my-repo", ["java"], user)

    assert exc_info.value.status_code == 400


async def test_analyze_file_rejects_non_zip(user: User) -> None:
    """Non-zip filenames are rejected with HTTP 400."""
    with pytest.raises(HTTPException) as exc_info:
        await analyze_file("readme.txt", MagicMock(), "my-repo", ["java"], user)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Only .zip files are accepted."


# ---------------------------------------------------------------------------
# delete_repo
# ---------------------------------------------------------------------------


async def test_delete_repo_decrements_count(
    persisted_repo_doc: RepoDocument,
    persisted_user_repo_doc: UserRepoDocument,
) -> None:
    """Deletes children, then UserRepoDocument, then decrements user_count."""
    await delete_repo(persisted_user_repo_doc, persisted_repo_doc)

    refreshed = await RepoDocument.get(persisted_repo_doc.id)
    assert refreshed is not None
    assert refreshed.user_count == 0

    user_repo = await UserRepoDocument.get(persisted_user_repo_doc.id)
    assert user_repo is None


# ---------------------------------------------------------------------------
# get_pipeline
# ---------------------------------------------------------------------------


async def test_get_pipeline_returns_latest(
    persisted_repo_doc: RepoDocument,
) -> None:
    """Returns the most recent PipelineRunDocument for the repository."""
    run = PipelineRunDocument(repo_id=persisted_repo_doc.id)
    await run.insert()

    result = await get_pipeline(persisted_repo_doc.id)  # type: ignore[arg-type]

    assert result.id == run.id


async def test_get_pipeline_raises_404_when_not_found(
    repo_id: PydanticObjectId,
) -> None:
    """Raises HTTP 404 when no pipeline run exists."""
    with pytest.raises(HTTPException) as exc_info:
        await get_pipeline(repo_id)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "No pipeline run found for this repository."


# ---------------------------------------------------------------------------
# get_file_documentation
# ---------------------------------------------------------------------------


async def test_get_file_documentation_found(
    persisted_file_doc: FileDocument,
) -> None:
    """Returns documentation when it exists."""
    doc = FileDocumentationDocument(
        repo_id=persisted_file_doc.repo_id,
        file_id=persisted_file_doc.id,
        content="Generated docs.",
    )
    await doc.insert()

    result = await get_file_documentation(
        persisted_file_doc.repo_id,
        persisted_file_doc.id,  # type: ignore[arg-type]
    )

    assert result.content == "Generated docs."


async def test_get_file_documentation_not_found(
    repo_id: PydanticObjectId,
    file_id: PydanticObjectId,
) -> None:
    """Raises HTTP 404 when no documentation exists."""
    with pytest.raises(HTTPException) as exc_info:
        await get_file_documentation(repo_id, file_id)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "No documentation found for this file."


# ---------------------------------------------------------------------------
# get_file_graphs
# ---------------------------------------------------------------------------


async def test_get_file_graphs_returns_all_graphs(
    repo_id: PydanticObjectId,
    file_id: PydanticObjectId,
) -> None:
    """Returns AST, CFG list, and DFG list."""
    await ASTDocument(repo_id=repo_id, file_id=file_id, content={"nodes": []}).insert()
    await CFGDocument(
        repo_id=repo_id, file_id=file_id, scope="main", content={"edges": []}
    ).insert()
    await DFGDocument(
        repo_id=repo_id, file_id=file_id, scope=None, content={"vars": []}
    ).insert()

    ast, cfgs, dfgs = await get_file_graphs(repo_id, file_id)

    assert ast is not None
    assert ast.content == {"nodes": []}
    assert len(cfgs) == 1
    assert cfgs[0].scope == "main"
    assert len(dfgs) == 1


async def test_get_file_graphs_returns_none_ast_and_empty_lists(
    repo_id: PydanticObjectId,
    file_id: PydanticObjectId,
) -> None:
    """Returns `None` for AST and empty CFG/DFG lists when no graphs exist."""
    ast, cfgs, dfgs = await get_file_graphs(repo_id, file_id)

    assert ast is None
    assert cfgs == []
    assert dfgs == []


# ---------------------------------------------------------------------------
# get_file_source
# ---------------------------------------------------------------------------


async def test_get_file_source_downloads_and_decodes(
    mock_repo_doc: MagicMock,
    mock_file_doc: MagicMock,
) -> None:
    """Downloads the blob and decodes it as a string."""
    mock_downloader = AsyncMock()
    mock_downloader.readall.return_value = b"public class Main {}"

    mock_container = AsyncMock()
    mock_container.download_blob.return_value = mock_downloader

    with patch("app.repos.service.get_container_client", return_value=mock_container):
        result = await get_file_source(mock_repo_doc, mock_file_doc)

    expected_path = f"{mock_repo_doc.blob_path}/{mock_file_doc.path}"
    mock_container.download_blob.assert_awaited_once_with(expected_path)
    assert result == "public class Main {}"


# ---------------------------------------------------------------------------
# get_files
# ---------------------------------------------------------------------------


async def test_get_files_returns_matching(
    persisted_file_doc: FileDocument,
    repo_id: PydanticObjectId,
) -> None:
    """Returns all files for the given repository."""
    result = await get_files(repo_id)

    assert len(result) == 1
    assert result[0].id == persisted_file_doc.id


# ---------------------------------------------------------------------------
# get_repo_meta
# ---------------------------------------------------------------------------


async def test_get_repo_meta_found(persisted_repo_doc: RepoDocument) -> None:
    """Returns RepoMetaDocument when it exists."""
    meta = RepoMetaDocument(
        repo_id=persisted_repo_doc.id,
        content="Repository overview.",
        stats=AnalysisStats(files_detected=3, ast_success=2),
    )
    await meta.insert()

    result = await get_repo_meta(persisted_repo_doc.id)  # type: ignore[arg-type]

    assert result is not None
    assert result.content == "Repository overview."
    assert result.stats.files_detected == 3
    assert result.stats.ast_success == 2


async def test_get_repo_meta_not_found(repo_id: PydanticObjectId) -> None:
    """Returns None when no meta document exists."""
    result = await get_repo_meta(repo_id)

    assert result is None


# ---------------------------------------------------------------------------
# get_repos
# ---------------------------------------------------------------------------


async def test_get_repos_joins_correctly(
    user: User,
    persisted_repo_doc: RepoDocument,
    persisted_user_repo_doc: UserRepoDocument,
) -> None:
    """Returns (repo, user_repo) tuples joined correctly."""
    result = await get_repos(user)

    assert len(result) == 1
    repo, user_repo = result[0]
    assert repo.id == persisted_repo_doc.id
    assert user_repo.id == persisted_user_repo_doc.id


async def test_get_repos_returns_empty_when_no_repos(user: User) -> None:
    """Returns an empty list when the user has no repositories."""
    result = await get_repos(user)

    assert result == []


async def test_get_repos_returns_newest_first(
    user: User,
    two_persisted_user_repo_docs: tuple[UserRepoDocument, UserRepoDocument],
) -> None:
    """Returns repos ordered by `UserRepoDocument` creation time, newest first."""
    ur_a, ur_b = two_persisted_user_repo_docs

    result = await get_repos(user)

    assert len(result) == 2
    assert result[0][1].id == ur_b.id  # newest user-repo first
    assert result[1][1].id == ur_a.id


@pytest.mark.parametrize(
    ("search", "expected_count"),
    [
        ("repo-a", 1),
        ("REPO-A", 1),
        ("repo", 2),
        ("nonexistent", 0),
        (None, 2),
    ],
    ids=["exact", "case_insensitive", "partial", "no_match", "no_filter"],
)
@pytest.mark.usefixtures("two_persisted_user_repo_docs")
async def test_get_repos_search(
    user: User,
    search: str | None,
    expected_count: int,
) -> None:
    """Filters repos by case-insensitive substring match on name."""
    result = await get_repos(user, search)

    assert len(result) == expected_count


# ---------------------------------------------------------------------------
# join_repo
# ---------------------------------------------------------------------------


async def test_join_repo_conflict(
    persisted_repo_doc: RepoDocument,
    persisted_user_repo_doc: UserRepoDocument,
    user: User,
) -> None:
    """Raises HTTP 409 when the user already has the repository."""
    with pytest.raises(HTTPException) as exc_info:
        await join_repo(persisted_repo_doc.id, user)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409


async def test_join_repo_raises_404_when_source_not_found(
    repo_id: PydanticObjectId,
    user: User,
) -> None:
    """Raises HTTP 404 when no source user-repo link exists."""
    with (
        patch.object(
            UserRepoDocument, "find_one", new_callable=AsyncMock, return_value=None
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await join_repo(repo_id, user)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Repository not found."


async def test_join_repo_rollback_garbage_collected(
    persisted_user_repo_doc: UserRepoDocument,
    repo_id: PydanticObjectId,
    user_alt: User,
) -> None:
    """Raises HTTP 404 and rolls back when the repo has user_count <= 0."""
    # Create a repo with user_count=0 (garbage-collected)
    gc_repo = RepoDocument(
        id=repo_id,
        blob_path="gc/path",
        languages=["java"],
        user_count=0,
    )
    await gc_repo.insert()

    with pytest.raises(HTTPException) as exc_info:
        await join_repo(repo_id, user_alt)

    assert exc_info.value.status_code == 404

    # Verify the user-repo link was rolled back
    rollback_check = await UserRepoDocument.find_one(
        UserRepoDocument.user_id == user_alt.uid
    )
    assert rollback_check is None


async def test_join_repo_success(
    persisted_repo_doc: RepoDocument,
    persisted_user_repo_doc: UserRepoDocument,
    user_alt: User,
) -> None:
    """Imports a repo: increments user_count and creates a new UserRepoDocument."""
    name = await join_repo(persisted_repo_doc.id, user_alt)  # type: ignore[arg-type]

    assert name == "test-repo"

    refreshed = await RepoDocument.get(persisted_repo_doc.id)
    assert refreshed is not None
    assert refreshed.user_count == 2

    new_ur = await UserRepoDocument.find_one(UserRepoDocument.user_id == user_alt.uid)
    assert new_ur is not None
    assert new_ur.name == "test-repo"


# ---------------------------------------------------------------------------
# update_user_repo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("initial_color", "color", "name", "fields_set", "expected_color", "expected_name"),
    [
        pytest.param(
            None, "#00ff00", None, {"color"}, "#00ff00", "test-repo", id="set_color"
        ),
        pytest.param(None, None, "new-name", {"name"}, None, "new-name", id="set_name"),
        pytest.param(
            None,
            "#00ff00",
            "new-name",
            {"color", "name"},
            "#00ff00",
            "new-name",
            id="set_both",
        ),
        pytest.param(
            None,
            "#00ff00",
            None,
            {"color"},
            "#00ff00",
            "test-repo",
            id="name_unchanged_when_omitted",
        ),
        pytest.param(
            "#ff0000",
            None,
            "new-name",
            {"name"},
            "#ff0000",
            "new-name",
            id="color_unchanged_when_omitted",
        ),
        pytest.param(
            "#ff0000", None, None, {"color"}, None, "test-repo", id="clear_color"
        ),
    ],
)
async def test_update_user_repo(
    persisted_user_repo_doc: UserRepoDocument,
    initial_color: str | None,
    color: str | None,
    name: str | None,
    fields_set: set[str],
    expected_color: str | None,
    expected_name: str,
) -> None:
    """Only fields present in fields_set are applied; omitted fields are unchanged."""
    if initial_color is not None:
        persisted_user_repo_doc.color = initial_color
        await persisted_user_repo_doc.save()

    result = await update_user_repo(persisted_user_repo_doc, color, name, fields_set)

    assert result.color == expected_color
    assert result.name == expected_name

    refreshed = await UserRepoDocument.get(persisted_user_repo_doc.id)
    assert refreshed is not None
    assert refreshed.color == expected_color
    assert refreshed.name == expected_name
