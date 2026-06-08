"""Pipeline activities.

This module defines the activity functions for the pipeline blueprint.
"""

import asyncio
import json

from azure.durable_functions import Blueprint
from beanie import PydanticObjectId

from app.core.language_enum import Language
from app.pipeline.config import pipeline_settings
from app.pipeline.models import PipelineStatus
from app.pipeline.service import (
    compute_file_representations,
    compute_repo_representations,
    stream_extract_zip,
    update_pipeline_run,
)

pipeline_activity_bp = Blueprint()


@pipeline_activity_bp.activity_trigger(input_name="payload")
async def patch_pipeline_run(payload: str) -> bool:
    """Retrieve a PipelineRunDocument and adjust its data.

    Args:
        payload(str): JSON string with key `pipeline_run_id`, optional
            `status` (RUNNING, COMPLETED, or FAILED) and `meta` string
            for additional info (e.g. error message).

    Returns:
        bool: True when the update succeeds.
    """
    data = json.loads(payload)
    pipeline_run_id = PydanticObjectId(data["pipeline_run_id"])
    status = PipelineStatus(data["status"]) if "status" in data else None
    meta = data.get("meta", None)

    await update_pipeline_run(
        pipeline_run_id=pipeline_run_id,
        status=status,
        meta=meta,
    )

    return True


@pipeline_activity_bp.activity_trigger(input_name="payload")
async def extract_zip(payload: str) -> list[str]:
    """Extract a zip from blob storage and upload matching files back.

    Runs a 3-stage streaming pipeline so the full zip never resides in
    memory at once:

      async download → queue → sync stream_unzip (thread) → queue → async upload

    Also updates the pipeline run status to RUNNING before extraction
    and reports the file count after extraction, avoiding two extra
    orchestrator replay cycles.

    Args:
        payload(str): JSON string with keys `blob_path`, `language`,
            and `pipeline_run_id`.

    Returns:
        list[str]: list of blob paths for the extracted files.
    """
    data = json.loads(payload)
    blob_path: str = data["blob_path"]
    language = Language(data["language"])
    pipeline_run_id = PydanticObjectId(data["pipeline_run_id"])

    await update_pipeline_run(
        pipeline_run_id=pipeline_run_id,
        status=PipelineStatus.RUNNING,
        meta="Extracting the ZIP...",
    )

    uploaded_paths = await stream_extract_zip(blob_path, language)

    await update_pipeline_run(
        pipeline_run_id=pipeline_run_id,
        meta=f"{len(uploaded_paths)} files extracted. Analyzing files...",
    )

    return uploaded_paths


@pipeline_activity_bp.activity_trigger(input_name="payload")
async def process_file_batch(payload: str) -> list[dict[str, str | int]]:
    """Analyze a batch of files: create file docs and build all graphs.

    Each file is processed independently so a failure in one does not
    block the others.

    Args:
        payload(str): JSON string with keys `repo_id`, `language`,
            and `blob_paths` (list of blob storage paths).

    Returns:
        list[dict]: One entry per file:
            `{"file_id": str, "ast": int, "cfg_succeeded": int, "cfg_failed": int,
            "dfg_succeeded": int, "dfg_failed": int, "doc": int}`.
            `"*_succeeded"/"*_failed"` count graph documents.
            `"*_failed == -1"` signals the upstream builder returned None.
    """
    data = json.loads(payload)
    repo_id = PydanticObjectId(data["repo_id"])
    language = Language(data["language"])
    blob_paths: list[str] = data["blob_paths"]

    sem = asyncio.Semaphore(pipeline_settings.ANALYZE_FILE_CONCURRENCY)

    async def _guarded(bp: str) -> dict[str, str | int]:
        async with sem:
            return await compute_file_representations(bp, repo_id, language)

    results = list(await asyncio.gather(*[_guarded(bp) for bp in blob_paths]))

    return results


@pipeline_activity_bp.activity_trigger(input_name="payload")
async def process_holistic_analysis(payload: str) -> bool:
    """Analyze the repository holistically after all files have been processed.

    It also updates the pipeline run status to COMPLETED, which is the last step
    of the pipeline.

    Args:
        payload(str): JSON string with keys `repo_id`, `pipeline_run_id`,
            `total_files`, `ast_success`, `ast_failed`, `cfg_success`,
            `cfg_failed`, `cfg_build_failed`, `dfg_success`, `dfg_failed`,
            `dfg_build_failed`, `doc_success`, `doc_failed`, `rag_success`,
            `rag_failed`.

    Returns:
        bool: True when the operation succeeds.
    """
    data = json.loads(payload)
    repo_id = PydanticObjectId(data["repo_id"])
    pipeline_run_id = PydanticObjectId(data["pipeline_run_id"])

    stats = {
        "total_files": data["total_files"],
        "ast_success": data["ast_success"],
        "ast_failed": data["ast_failed"],
        "cfg_success": data["cfg_success"],
        "cfg_failed": data["cfg_failed"],
        "cfg_build_failed": data["cfg_build_failed"],
        "dfg_success": data["dfg_success"],
        "dfg_failed": data["dfg_failed"],
        "dfg_build_failed": data["dfg_build_failed"],
        "doc_success": data["doc_success"],
        "doc_failed": data["doc_failed"],
    }

    await compute_repo_representations(repo_id, stats)

    await update_pipeline_run(
        pipeline_run_id=pipeline_run_id,
        status=PipelineStatus.COMPLETED,
        meta="Pipeline completed successfully.",
    )

    return True
