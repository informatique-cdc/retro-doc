"""Pipeline orchestrators.

This module defines the orchestrator functions for the pipeline blueprint.
"""

import json
import posixpath
from collections import Counter
from itertools import chain
from typing import Any

from azure.durable_functions import Blueprint
from azure.durable_functions.models import DurableOrchestrationContext, RetryOptions

from app.core.language import Language, get_language_from_path
from app.pipeline.config import pipeline_settings
from app.pipeline.schemas import FileResult

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
    languages = payload.get("languages") or []
    repo_id = payload["repo_id"]
    pipeline_run_id = payload["pipeline_run_id"]

    # Empty list = "all supported languages"; a non-empty list is a filter
    targeted: set[str] = set(languages)

    try:
        # Step 1: Extract zip and get list of file blob paths
        file_paths: list[str] = yield context.call_activity_with_retry(
            "extract_zip",
            _retry,
            json.dumps(
                {
                    "blob_path": blob_path,
                    "pipeline_run_id": pipeline_run_id,
                }
            ),
        )

        # Step 2: Partition files into analyzable (supported & targeted) ones,
        # grouped by detected language, and "other" files which are only
        # cataloged (FileDocument) without further analysis
        analyzable_by_language: dict[Language, list[str]] = {}
        other_paths: list[str] = []
        for path in file_paths:
            detected = get_language_from_path(path)
            if detected is not None and (not targeted or detected.value in targeted):
                analyzable_by_language.setdefault(detected, []).append(path)
            else:
                other_paths.append(path)

        # Batched per-file analysis (fan-out / fan-in), per language
        batch_size = pipeline_settings.ANALYZE_BATCH_SIZE
        analyze_tasks = [
            context.call_activity_with_retry(
                "process_file_batch",
                _retry,
                json.dumps(
                    {
                        "repo_id": repo_id,
                        "language": language.value,
                        "blob_paths": paths[i : i + batch_size],
                    }
                ),
            )
            for language, paths in analyzable_by_language.items()
            for i in range(0, len(paths), batch_size)
        ]

        # Catalog the remaining files (not analyzable) in batches, without further analysis
        file_doc_tasks = [
            context.call_activity_with_retry(
                "persist_file_docs",
                _retry,
                json.dumps(
                    {
                        "repo_id": repo_id,
                        "blob_paths": other_paths[i : i + batch_size],
                    }
                ),
            )
            for i in range(0, len(other_paths), batch_size)
        ]

        catalog_file_success = 0
        catalog_file_failed = 0
        if file_doc_tasks:
            file_doc_results: list[dict[str, int]] = yield context.task_all(
                file_doc_tasks
            )
            catalog_file_success = sum(r["file_success"] for r in file_doc_results)
            catalog_file_failed = sum(r["file_failed"] for r in file_doc_results)

        if analyze_tasks:
            batch_results: list[list[FileResult]] = yield context.task_all(
                analyze_tasks
            )
            results: list[FileResult] = list(chain.from_iterable(batch_results))
        else:
            results = []

        # Per-extension breakdown of every detected file (analyzed or not)
        files_by_extension = dict(
            sorted(
                Counter(
                    posixpath.splitext(p)[1].lower() or "(none)" for p in file_paths
                ).items()
            )
        )

        stats: dict[str, Any] = {
            "files_detected": len(file_paths),
            "files_by_extension": files_by_extension,
            "file_success": catalog_file_success
            + sum(1 for r in results if r["file_persisted"]),
            "file_failed": catalog_file_failed
            + sum(1 for r in results if not r["file_persisted"]),
            "ast_success": sum(1 for r in results if r["ast_persisted"]),
            "ast_failed": sum(1 for r in results if not r["ast_persisted"]),
            "cfg_success": sum(r["cfg_persisted"] for r in results),
            "cfg_failed": sum(r["cfg_failed"] for r in results),
            "cfg_build_failed": sum(1 for r in results if not r["cfg_built"]),
            "dfg_success": sum(r["dfg_persisted"] for r in results),
            "dfg_failed": sum(r["dfg_failed"] for r in results),
            "dfg_build_failed": sum(1 for r in results if not r["dfg_built"]),
            "doc_success": sum(1 for r in results if r["doc_persisted"]),
            "doc_failed": sum(1 for r in results if not r["doc_persisted"]),
            "rag_success": sum(
                1 for r in results if r["doc_persisted"] and r["rag_persisted"]
            ),
            "rag_failed": sum(
                1 for r in results if r["doc_persisted"] and not r["rag_persisted"]
            ),
        }

        # Integrity gate: the FileDocument catalog is the base everything else
        # hangs off (the consumer's file tree is this catalog). A run that
        # detected files but persisted none is worthless, so fail it instead of
        # reporting COMPLETED. A partial catalog is tolerated (counts in stats)
        if stats["files_detected"] > 0 and stats["file_success"] == 0:
            raise RuntimeError(
                f"Pipeline: no FileDocument persisted for repo '{repo_id}' "
                f"({stats['files_detected']} file(s) detected)"
            )

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

    except Exception:
        yield context.call_activity_with_retry(
            "patch_pipeline_run",
            _retry,
            json.dumps(
                {
                    "pipeline_run_id": pipeline_run_id,
                    "status": "failed",
                }
            ),
        )
        raise

    return stats
