"""Unit test configuration for pipeline.

This module provides fixtures for pipeline service tests,
including HTTP mocking and persisted pipeline run documents.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from beanie import PydanticObjectId

from app.pipeline.models import PipelineRunDocument


@pytest.fixture
def mock_pipeline_run_doc() -> MagicMock:
    """A mock `PipelineRunDocument` with preset `id` and `repo_id`."""
    run = MagicMock(spec=PipelineRunDocument)
    run.id = PydanticObjectId("aaaaaaaaaaaaaaaaaaaaaaaa")
    run.repo_id = PydanticObjectId("bbbbbbbbbbbbbbbbbbbbbbbb")
    run.set = AsyncMock()
    return run


@pytest.fixture
def mock_httpx_connect_error() -> Any:
    """Patch `httpx.AsyncClient.post` to raise `ConnectError`."""
    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.ConnectError("Connection refused")
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    with patch.object(httpx, "AsyncClient", return_value=mock_client):
        yield mock_client


@pytest.fixture
def mock_httpx_missing_id() -> Any:
    """Patch `httpx.AsyncClient` to return a response missing the `id` key."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"no_id": "value"}
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    with patch.object(httpx, "AsyncClient", return_value=mock_client):
        yield mock_client


@pytest.fixture
def mock_httpx_success() -> Any:
    """Patch `httpx.AsyncClient` to return a 200 response with `{"id": "..."}`."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"id": "instance-123"}
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    with patch.object(httpx, "AsyncClient", return_value=mock_client):
        yield mock_client


@pytest.fixture
async def persisted_pipeline_run_doc(
    repo_id: PydanticObjectId,
) -> PipelineRunDocument:
    """A persisted `PipelineRunDocument` in mongomock."""
    doc = PipelineRunDocument(repo_id=repo_id)
    await doc.insert()
    return doc
