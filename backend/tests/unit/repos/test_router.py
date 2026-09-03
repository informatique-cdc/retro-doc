"""Unit tests for repos router.

This module tests the repos router using `httpx.AsyncClient`
with mocked business logic and dependency overrides, plus direct
endpoint-function tests that don't need HTTP transport.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException, UploadFile

from app.auth.schemas import User
from app.docs.models import AnalysisStats, RepoMetaDocument
from app.repos.router import create_repo_endpoint

pytestmark = pytest.mark.usefixtures("_override_deps")

# ---------------------------------------------------------------------------
# POST /repos/
# ---------------------------------------------------------------------------


async def test_analyze_file_success(
    mock_client: httpx.AsyncClient,
    repo_id: PydanticObjectId,
) -> None:
    """Successful file analysis returns 202."""
    with (
        patch(
            "app.repos.dependencies.get_supported_languages",
            new_callable=AsyncMock,
            return_value=["java"],
        ),
        patch(
            "app.repos.router.analyze_file",
            new_callable=AsyncMock,
            return_value=repo_id,
        ),
    ):
        resp = await mock_client.post(
            "/repos",
            files={"file": ("code.zip", b"fake-zip-content", "application/zip")},
            data={"name": "my-repo", "languages": ["java"]},
        )

    assert resp.status_code == 202
    data = resp.json()
    assert data["repo_id"] == str(repo_id)
    assert data["status"] == "pending"


async def test_analyze_file_all_languages_when_omitted(
    mock_client: httpx.AsyncClient,
    repo_id: PydanticObjectId,
) -> None:
    """Omitting languages ('analyze all') succeeds without calling the worker."""
    with (
        patch(
            "app.repos.dependencies.get_supported_languages",
            new_callable=AsyncMock,
        ) as mock_supported,
        patch(
            "app.repos.router.analyze_file",
            new_callable=AsyncMock,
            return_value=repo_id,
        ),
    ):
        resp = await mock_client.post(
            "/repos",
            files={"file": ("code.zip", b"fake-zip-content", "application/zip")},
            data={"name": "my-repo"},
        )

    assert resp.status_code == 202
    mock_supported.assert_not_awaited()


async def test_analyze_file_rejects_unsupported_language(
    mock_client: httpx.AsyncClient,
) -> None:
    """An unsupported language is rejected with 422 (before any upload)."""
    with patch(
        "app.repos.dependencies.get_supported_languages",
        new_callable=AsyncMock,
        return_value=["java"],
    ):
        resp = await mock_client.post(
            "/repos",
            files={"file": ("code.zip", b"fake-zip-content", "application/zip")},
            data={"name": "my-repo", "languages": ["cobol"]},
        )

    assert resp.status_code == 422
    assert "cobol" in resp.json()["detail"]


async def test_analyze_file_missing_file(mock_client: httpx.AsyncClient) -> None:
    """Missing file field returns 422."""
    resp = await mock_client.post(
        "/repos",
        data={"name": "my-repo"},
    )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /repos/{repo_id}
# ---------------------------------------------------------------------------


async def test_get_repo_with_meta(
    mock_client: httpx.AsyncClient,
    repo_id: PydanticObjectId,
) -> None:
    """Returns repo details with meta content and stats."""
    mock_meta = MagicMock(spec=RepoMetaDocument)
    mock_meta.content = "Repository overview."
    mock_meta.stats = AnalysisStats(files_detected=3, ast_success=2)

    with patch(
        "app.repos.router.get_repo_meta",
        new_callable=AsyncMock,
        return_value=mock_meta,
    ):
        resp = await mock_client.get(f"/repos/{repo_id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["repo_id"] == str(repo_id)
    assert data["content"] == "Repository overview."
    assert data["stats"]["files_detected"] == 3
    assert data["stats"]["ast_success"] == 2


async def test_get_repo_without_meta(
    mock_client: httpx.AsyncClient,
    repo_id: PydanticObjectId,
) -> None:
    """Returns repo details with content=null and stats=null when no meta exists."""
    with patch(
        "app.repos.router.get_repo_meta",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await mock_client.get(f"/repos/{repo_id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] is None
    assert data["stats"] is None


# ---------------------------------------------------------------------------
# Direct endpoint-function tests
# ---------------------------------------------------------------------------


async def test_create_repo_endpoint_raises_400_when_filename_is_none(
    user: User,
) -> None:
    """Raises HTTP 400 when `UploadFile.filename` is `None`."""
    mock_file = MagicMock(spec=UploadFile)
    mock_file.filename = None

    with pytest.raises(HTTPException) as exc_info:
        await create_repo_endpoint(
            file=mock_file,
            user=user,
            languages=["java"],
            name="my-repo",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Filename is required"
