"""Unit tests for pipeline service.

This module tests the start_orchestration service function, both
with pure mocks and against a mongomock database.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.pipeline.models import PipelineRunDocument, PipelineStatus
from app.pipeline.service import start_orchestration

# ---------------------------------------------------------------------------
# start_orchestration
# ---------------------------------------------------------------------------


async def test_connect_error_marks_run_as_failed(
    mock_httpx_connect_error: Any,
    persisted_pipeline_run_doc: PipelineRunDocument,
    blob_path: str,
) -> None:
    """On connection error, the `PipelineRunDocument` status is set to `FAILED` in the database."""
    with pytest.raises(HTTPException) as exc_info:
        await start_orchestration(blob_path, ["java"], persisted_pipeline_run_doc)

    assert exc_info.value.status_code == 502

    refreshed = await PipelineRunDocument.get(persisted_pipeline_run_doc.id)
    assert refreshed is not None
    assert refreshed.status == PipelineStatus.FAILED


async def test_missing_id_key_raises_502(
    mock_httpx_missing_id: Any,
    mock_pipeline_run_doc: MagicMock,
    blob_path: str,
) -> None:
    """Response JSON missing the `id` key produces HTTP 502 and marks run as failed."""
    with pytest.raises(HTTPException) as exc_info:
        await start_orchestration(blob_path, ["java"], mock_pipeline_run_doc)

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Failed to start the analysis pipeline."
    mock_pipeline_run_doc.set.assert_awaited_once()
    update_dict = mock_pipeline_run_doc.set.call_args.args[0]
    assert PipelineStatus.FAILED in update_dict.values()


async def test_success_preserves_pending_status(
    mock_httpx_success: Any,
    persisted_pipeline_run_doc: PipelineRunDocument,
    blob_path: str,
) -> None:
    """On success, the `PipelineRunDocument` status remains `PENDING` in the database."""
    await start_orchestration(blob_path, ["java"], persisted_pipeline_run_doc)

    refreshed = await PipelineRunDocument.get(persisted_pipeline_run_doc.id)
    assert refreshed is not None
    assert refreshed.status == PipelineStatus.PENDING


async def test_unprocessable_notifies_unsupported_languages(
    mock_httpx_unprocessable: Any,
    persisted_pipeline_run_doc: PipelineRunDocument,
    blob_path: str,
) -> None:
    """A worker 422 (rejected languages) yields HTTP 422 with a clear message and FAILED meta."""
    with pytest.raises(HTTPException) as exc_info:
        await start_orchestration(blob_path, ["cobol"], persisted_pipeline_run_doc)

    assert exc_info.value.status_code == 422
    assert "languages" in exc_info.value.detail.lower()

    refreshed = await PipelineRunDocument.get(persisted_pipeline_run_doc.id)
    assert refreshed is not None
    assert refreshed.status == PipelineStatus.FAILED
    assert refreshed.meta is not None
    assert "languages" in refreshed.meta.message.lower()
