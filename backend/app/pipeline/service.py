"""Pipeline service.

This module provides a client to start orchestrations on an Azure Durable
Functions app via its HTTP API.
"""

import httpx
from fastapi import HTTPException, status
from loguru import logger

from app.pipeline.config import pipeline_settings
from app.pipeline.models import PipelineMeta, PipelineRunDocument, PipelineStatus


async def start_orchestration(
    blob_path: str, languages: list[str], pipeline_run: PipelineRunDocument
) -> None:
    """Start the 'analyze' orchestration.

    Calls the Azure Durable Functions HTTP API to start a new orchestration
    instance that will process the zip file stored in blob storage.

    Args:
        blob_path(str): The Azure Blob Storage path of the uploaded zip file.
        languages(list[str]): The languages to analyze (empty = all supported).
        pipeline_run(PipelineRunDocument): The MongoDB PipelineRunDocument to
            send to the orchestrator for tracking.

    Raises:
        HTTPException: 422 if the worker rejects the requested languages, 502 if
            the Durable Functions endpoint is unreachable or returns an
            unexpected response.
    """
    path = "/api/pipeline"
    payload = {
        "blob_path": blob_path,
        "languages": languages,
        "pipeline_run_id": str(pipeline_run.id),
        "repo_id": str(pipeline_run.repo_id),
    }

    try:
        async with httpx.AsyncClient(
            base_url=pipeline_settings.DURABLE_FUNCTIONS_BASE_URL
        ) as client:
            response = await client.post(path, json=payload)
            response.raise_for_status()
        data = response.json()
        instance_id: str = data["id"]
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.exception("Pipeline: Failed to start orchestration.")
        # A 422 means the worker rejected the languages — in practice an
        # unsupported language (the cached list was stale). Notify the user.
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 422:
            message = "One or more selected languages seems no longer supported."
            status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
        else:
            message = "Failed to start the analysis pipeline."
            status_code = status.HTTP_502_BAD_GATEWAY
        await pipeline_run.set(
            {
                PipelineRunDocument.status: PipelineStatus.FAILED,
                PipelineRunDocument.meta: PipelineMeta(message=message, step="launch"),
            }
        )
        raise HTTPException(status_code=status_code, detail=message)

    logger.debug(
        f"Pipeline: Started orchestration {instance_id} for repo '{pipeline_run.repo_id}'."
    )
