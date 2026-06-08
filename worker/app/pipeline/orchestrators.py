"""Pipeline orchestrators.

This module defines the orchestrator functions for the pipeline blueprint.
"""

import json
from itertools import chain
from typing import Any

from azure.durable_functions import Blueprint
from azure.durable_functions.models import DurableOrchestrationContext, RetryOptions

from app.pipeline.config import pipeline_settings

pipeline_orch_bp = Blueprint()

_retry = RetryOptions(
    first_retry_interval_in_milliseconds=pipeline_settings.ANALYZE_RETRY_INTERVAL_MS,
    max_number_of_attempts=pipeline_settings.ANALYZE_RETRY_ATTEMPTS,
)


@pipeline_orch_bp.orchestration_trigger(context_name="context")
def analyze(context: DurableOrchestrationContext) -> Any:
    """Orchestrate the analysis pipeline.

    Args:
        context(DurableOrchestrationContext): The orchestration context.

    Returns:
        list: Results from the activity calls.
    """
    payload = context.get_input()
    blob_path = payload["blob_path"]
    language = payload["language"]
    repo_id = payload["repo_id"]
    pipeline_run_id = payload["pipeline_run_id"]

    try:
        # Step 1: Extract zip and get list of file blob paths
        file_paths: list[str] = yield context.call_activity_with_retry(
            "extract_zip",
            _retry,
            json.dumps(
                {
                    "blob_path": blob_path,
                    "language": language,
                    "pipeline_run_id": pipeline_run_id,
                }
            ),
        )

        # Step 2: Batched per-file analysis (fan-out / fan-in)
        n_files = len(file_paths)
        batch_size = pipeline_settings.ANALYZE_BATCH_SIZE
        batches = [
            file_paths[i : i + batch_size] for i in range(0, n_files, batch_size)
        ]
        analyze_tasks = [
            context.call_activity_with_retry(
                "process_file_batch",
                _retry,
                json.dumps(
                    {
                        "repo_id": repo_id,
                        "language": language,
                        "blob_paths": batch,
                    }
                ),
            )
            for batch in batches
        ]
        if analyze_tasks:
            batch_results: list[list[dict[str, str | int]]] = yield context.task_all(
                analyze_tasks
            )
            results: list[dict[str, str | int]] = list(
                chain.from_iterable(batch_results)
            )
        else:
            results = []
        stats = {
            "total_files": n_files,
            "ast_success": sum(1 for r in results if r["ast"] >= 0),  # type: ignore
            "ast_failed": sum(1 for r in results if r["ast"] < 0),  # type: ignore
            "cfg_success": sum(
                r["cfg_succeeded"]
                for r in results
                if r["cfg_failed"] >= 0  # type: ignore
            ),
            "cfg_failed": sum(r["cfg_failed"] for r in results if r["cfg_failed"] >= 0),  # type: ignore
            "cfg_build_failed": sum(1 for r in results if r["cfg_failed"] < 0),  # type: ignore
            "dfg_success": sum(
                r["dfg_succeeded"]
                for r in results
                if r["dfg_failed"] >= 0  # type: ignore
            ),
            "dfg_failed": sum(r["dfg_failed"] for r in results if r["dfg_failed"] >= 0),  # type: ignore
            "dfg_build_failed": sum(1 for r in results if r["dfg_failed"] < 0),  # type: ignore
            "doc_success": sum(1 for r in results if r["doc"] >= 0),  # type: ignore
            "doc_failed": sum(1 for r in results if r["doc"] < 0),  # type: ignore
            "rag_success": sum(1 for r in results if r["rag"] >= 0),  # type: ignore
            "rag_failed": sum(1 for r in results if r["rag"] < 0),  # type: ignore
        }

        # Step 3: Holistic analysis
        yield context.call_activity_with_retry(
            "process_holistic_analysis",
            _retry,
            json.dumps(
                {
                    "repo_id": repo_id,
                    "pipeline_run_id": pipeline_run_id,
                    **stats,
                }
            ),
        )

    except Exception as e:
        yield context.call_activity_with_retry(
            "patch_pipeline_run",
            _retry,
            json.dumps(
                {
                    "pipeline_run_id": pipeline_run_id,
                    "status": "failed",
                    "meta": str(e),
                }
            ),
        )
        raise

    return stats
