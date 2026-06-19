"""Pipeline service.

This module provides a client to start orchestrations on an Azure Durable
Functions app via its HTTP API.
"""

import httpx
from fastapi import HTTPException, status
from loguru import logger

from app.core.language_enum import Language
from app.pipeline.config import pipeline_settings
from app.pipeline.models import PipelineMeta, PipelineRunDocument, PipelineStatus


async def start_orchestration(
    blob_path: str, language: Language, pipeline_run: PipelineRunDocument
) -> None:
    """Start the 'analyze' orchestration.

    Calls the Azure Durable Functions HTTP API to start a new orchestration
    instance that will process the zip file stored in blob storage.

    Args:
        blob_path(str): The Azure Blob Storage path of the uploaded zip file.
        language(Language): The programming language to analyze.
        pipeline_run(PipelineRunDocument): The MongoDB PipelineRunDocument to
            send to the orchestrator for tracking.

    Raises:
        HTTPException: 502 if the Durable Functions endpoint is unreachable or
            returns an unexpected response.
    """
    path = "/api/pipeline"
    payload = {
        "blob_path": blob_path,
        "language": language.value,
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
        logger.exception(f"Pipeline: Failed to start orchestration.")
        await pipeline_run.set(
            {
                PipelineRunDocument.status: PipelineStatus.FAILED,
                PipelineRunDocument.meta: PipelineMeta(
                    message="Failed to start orchestration.", step="launch"
                ),
            }
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to start the analysis pipeline.",
        )

    logger.debug(
        f"Pipeline: Started orchestration {instance_id} for repo '{pipeline_run.repo_id}'."
    )
