"""Pipeline activities.

This module defines the activity functions for the pipeline blueprint.
"""

import asyncio
import json

from azure.durable_functions import Blueprint
from beanie import PydanticObjectId
from loguru import logger

from app.core.language import Language
from app.pipeline.config import pipeline_settings
from app.pipeline.models import PipelineMeta, PipelineStatus
from app.pipeline.schemas import FileResult
from app.pipeline.service import (
    _skipped_file_result,
    compute_file_representations,
    compute_repo_representations,
    persist_file_documents,
    stream_extract_zip,
    update_pipeline_run,
)

pipeline_activity_bp = Blueprint()


@pipeline_activity_bp.activity_trigger(input_name="payload")
async def patch_pipeline_run(payload: str) -> None:
    """Retrieve a PipelineRunDocument and adjust its data.

    Args:
        payload(str): JSON string with key `pipeline_run_id` and optional
            `status` (RUNNING, COMPLETED, or FAILED).
    """
    data = json.loads(payload)
    pipeline_run_id = PydanticObjectId(data["pipeline_run_id"])
    status = PipelineStatus(data["status"]) if "status" in data else None

    await update_pipeline_run(
        pipeline_run_id=pipeline_run_id,
        status=status,
    )


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
        payload(str): JSON string with keys `blob_path` and `pipeline_run_id`.

    Returns:
        list[str]: list of blob paths for the extracted files.
    """
    data = json.loads(payload)
    blob_path: str = data["blob_path"]
    pipeline_run_id = PydanticObjectId(data["pipeline_run_id"])

    await update_pipeline_run(
        pipeline_run_id=pipeline_run_id,
        status=PipelineStatus.RUNNING,
        meta=PipelineMeta(message="Extracting the ZIP...", step="extract"),
    )

    uploaded_paths = await stream_extract_zip(blob_path)

    await update_pipeline_run(
        pipeline_run_id=pipeline_run_id,
        meta=PipelineMeta(
            message=f"{len(uploaded_paths)} file(s) extracted. Analyzing file(s)...",
            step="analyze",
        ),
    )

    return uploaded_paths


@pipeline_activity_bp.activity_trigger(input_name="payload")
async def process_file_batch(payload: str) -> list[FileResult]:
    """Analyze a batch of files: create file docs and build all graphs.

    Each file is processed independently so a failure in one does not
    block the others: a file whose `FileDocument` cannot be persisted (or
    that raises unexpectedly) is recorded as a skipped result and the rest
    of the batch proceeds.

    Args:
        payload(str): JSON string with keys `repo_id`, `language`,
            and `blob_paths` (list of blob storage paths).

    Returns:
        list[FileResult]: one entry per file; a skipped or errored file is recorded via
            `_skipped_file_result`.
    """
    data = json.loads(payload)
    repo_id = PydanticObjectId(data["repo_id"])
    language = Language(data["language"])
    blob_paths: list[str] = data["blob_paths"]

    sem = asyncio.Semaphore(pipeline_settings.ANALYZE_FILE_CONCURRENCY)

    async def _guarded(bp: str) -> FileResult:
        async with sem:
            return await compute_file_representations(bp, repo_id, language)

    raw_results = await asyncio.gather(
        *[_guarded(bp) for bp in blob_paths], return_exceptions=True
    )

    results: list[FileResult] = []
    for bp, res in zip(blob_paths, raw_results):
        if isinstance(res, BaseException):
            logger.opt(exception=res).error(
                f"Pipeline: Unexpected error analyzing '{bp}'"
            )
            results.append(_skipped_file_result())
        else:
            results.append(res)

    return results


@pipeline_activity_bp.activity_trigger(input_name="payload")
async def persist_file_docs(payload: str) -> dict[str, int]:
    """Create file documents for files that are kept but not analyzed.

    These are files whose detected language is not targeted (or not
    supported): they are recorded in the repository catalog without any
    further analysis.

    Args:
        payload(str): JSON string with keys `repo_id` and `blob_paths`
            (list of blob storage paths).

    Returns:
        dict[str, int]: `{"file_success": int, "file_failed": int}`.
    """
    data = json.loads(payload)
    repo_id = PydanticObjectId(data["repo_id"])
    blob_paths: list[str] = data["blob_paths"]

    succeeded, failed = await persist_file_documents(repo_id, blob_paths)

    return {"file_success": succeeded, "file_failed": failed}


@pipeline_activity_bp.activity_trigger(input_name="payload")
async def process_holistic_analysis(payload: str) -> None:
    """Analyze the repository holistically after all files have been processed.

    It also updates the pipeline run status to COMPLETED, which is the last step
    of the pipeline.

    Args:
        payload(str): JSON string with keys `repo_id`, `pipeline_run_id`,
            `files_detected`, `files_by_extension`, `file_success`,
            `file_failed`, `ast_success`, `ast_failed`, `cfg_success`,
            `cfg_failed`, `cfg_build_failed`, `dfg_success`, `dfg_failed`,
            `dfg_build_failed`, `doc_success`, `doc_failed`, `rag_success`,
            `rag_failed`.
    """
    data = json.loads(payload)
    repo_id = PydanticObjectId(data["repo_id"])
    pipeline_run_id = PydanticObjectId(data["pipeline_run_id"])

    stats = {
        "files_detected": data["files_detected"],
        "files_by_extension": data["files_by_extension"],
        "file_success": data["file_success"],
        "file_failed": data["file_failed"],
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
        "rag_success": data["rag_success"],
        "rag_failed": data["rag_failed"],
    }

    await update_pipeline_run(
        pipeline_run_id=pipeline_run_id,
        meta=PipelineMeta(message="Generating repository summary...", step="summarize"),
    )

    await compute_repo_representations(repo_id, stats)

    await update_pipeline_run(
        pipeline_run_id=pipeline_run_id,
        status=PipelineStatus.COMPLETED,
    )
