"""Pipeline service.

This module defines the service layer for the pipeline-related operations.
"""

import asyncio
import posixpath
import queue
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from loguru import logger

from app.core.blob_storage import get_container_client
from app.core.database import mongodb_retry, mongodb_retry_insert_many
from app.core.language import Language
from app.docs.models import AnalysisStats, FileDocumentationDocument
from app.docs.service import (
    create_repo_meta_summary,
    generate_documentation_file,
    persist_documentation,
    persist_repo_meta,
)
from app.graphs.models import CFGDocument, DFGDocument
from app.graphs.service import (
    get_graph_services,
    persist_ast,
    persist_scoped_graphs,
)
from app.pipeline.config import pipeline_settings
from app.pipeline.models import PipelineMeta, PipelineRunDocument, PipelineStatus
from app.pipeline.schemas import FileResult
from app.pipeline.utils import stream_download, stream_extract, stream_upload
from app.rag.service import index_file_documentation
from app.repos.models import FileDocument
from app.repos.service import persist_file


def _blob_path_to_relative(blob_path: str) -> str:
    """Derive a repository-relative path from a full blob storage path.

    Args:
        blob_path(str): The blob storage path of a file.

    Returns:
        str: The path relative to the repository root.
    """
    return posixpath.join(*blob_path.split("/")[5:])


def _skipped_file_result() -> FileResult:
    """Build the `FileResult` for a file that could not be analyzed.

    Used when a file's `FileDocument` cannot be persisted (no `file_id` to
    anchor graphs) or when an unexpected error escapes per-file processing.
    Nothing is persisted, so every artifact is marked accordingly.

    Returns:
        FileResult: a result with no artifact persisted (all booleans `False`,
            all counts `0`).
    """
    return FileResult(
        file_id="",
        file_persisted=False,
        ast_persisted=False,
        cfg_persisted=0,
        cfg_failed=0,
        cfg_built=False,
        dfg_persisted=0,
        dfg_failed=0,
        dfg_built=False,
        doc_persisted=False,
        rag_persisted=False,
    )


async def compute_file_representations(
    blob_path: str,
    repo_id: PydanticObjectId,
    language: Language,
) -> FileResult:
    """Create a file document, download the source once, build all graphs
    and documentation independently, and persist results.

    Each graph type (AST, CFG, DFG) is built independently so a failure
    in one does not block the others. If the `FileDocument` itself cannot be
    persisted, the file is skipped.

    Args:
        blob_path(str): The blob storage path of the source file.
        repo_id(PydanticObjectId): The repository ID.
        language(Language): The programming language.

    Returns:
        FileResult: the per-file outcome.
    """
    relative_path = _blob_path_to_relative(blob_path)

    # Step 1: Create FileDocument
    try:
        file_id = await persist_file(repo_id, relative_path, file_hash="")
    except Exception:
        logger.exception(f"Pipeline: FileDocument persist failed for '{blob_path}'")
        return _skipped_file_result()

    # Step 2: Download blob once
    source_code = await get_source_code_from_blob(blob_path)

    # Step 3: Build graphs independently:
    ast_data: dict[str, Any] | None = None
    cfg_results: list[dict[str, Any]] | None = None
    dfg_results: list[dict[str, Any]] | None = None

    ast_parser, cfg_builder, dfg_builder = get_graph_services(language)

    try:
        ast_data = ast_parser.parse(source_code, relative_path)
    except Exception:
        logger.exception(f"Pipeline: AST parse failed for '{blob_path}'")

    try:
        cfg_results = cfg_builder.build(source_code, relative_path)
    except Exception:
        logger.exception(f"Pipeline: CFG build failed for '{blob_path}'")

    try:
        dfg_results = dfg_builder.build(source_code, relative_path)
    except Exception:
        logger.exception(f"Pipeline: DFG build failed for '{blob_path}'")

    # Step 4: Persist all graphs concurrently
    ast_persisted, (cfg_ok, cfg_err), (dfg_ok, dfg_err) = await asyncio.gather(
        persist_ast(ast_data, repo_id, file_id, blob_path),
        persist_scoped_graphs(cfg_results, CFGDocument, repo_id, file_id, blob_path),
        persist_scoped_graphs(dfg_results, DFGDocument, repo_id, file_id, blob_path),
    )

    # Step 5: Generate documentation via LLM
    doc_persisted = False
    doc: str | None = None
    existing_doc: FileDocumentationDocument | None = None
    try:
        existing_doc = await FileDocumentationDocument.find_one(
            FileDocumentationDocument.repo_id == repo_id,
            FileDocumentationDocument.file_id == file_id,
        )
        if existing_doc:
            doc_persisted = True
        else:
            doc = await generate_documentation_file(
                relative_path,
                source_code,
                ast_data,
                cfg_results,
                dfg_results,
                language,
            )
            if doc:
                await persist_documentation(repo_id, file_id, doc)
                doc_persisted = True
            else:
                logger.warning(
                    f"Pipeline: Empty documentation generated for '{blob_path}'"
                )
    except Exception:
        logger.exception(f"Pipeline: Documentation generation failed for '{blob_path}'")

    # Step 6: Index documentation in vectorstore for RAG
    rag_persisted = False
    doc_content = doc if doc else (existing_doc.content if existing_doc else None)
    if doc_persisted and doc_content:
        rag_persisted = await index_file_documentation(
            repo_id=str(repo_id),
            file_id=str(file_id),
            file_path=relative_path,
            content=doc_content,
        )

    logger.debug(
        f"Pipeline: File '{file_id}' - AST={ast_persisted}, CFG=({cfg_ok}|{cfg_err}), DFG=({dfg_ok}|{dfg_err}), Doc={doc_persisted}, RAG={rag_persisted}"
    )

    return FileResult(
        file_id=str(file_id),
        file_persisted=True,
        ast_persisted=ast_persisted,
        cfg_persisted=cfg_ok,
        cfg_failed=cfg_err if cfg_results is not None else 0,
        cfg_built=cfg_results is not None,
        dfg_persisted=dfg_ok,
        dfg_failed=dfg_err if dfg_results is not None else 0,
        dfg_built=dfg_results is not None,
        doc_persisted=doc_persisted,
        rag_persisted=rag_persisted,
    )


async def persist_file_documents(
    repo_id: PydanticObjectId,
    blob_paths: list[str],
) -> tuple[int, int]:
    """Create a FileDocument for each blob path without any further analysis.

    Used for files that are kept in the repository catalog but are not
    analyzed (non-targeted language, unsupported extension, etc.).

    Args:
        repo_id(PydanticObjectId): The repository ID.
        blob_paths(list[str]): The blob storage paths of the files to record.

    Returns:
        tuple[int, int]: `(succeeded, failed)` counts.
    """
    docs = [
        FileDocument(
            repo_id=repo_id,
            path=_blob_path_to_relative(blob_path),
            file_hash="",
        )
        for blob_path in blob_paths
    ]
    succeeded, failed, error_codes = await mongodb_retry_insert_many(FileDocument, docs)
    if failed:
        logger.warning(
            f"Pipeline: {failed} file document(s) failed to persist for repo "
            f"'{repo_id}' (codes: {error_codes})"
        )
    return succeeded, failed


async def compute_repo_representations(
    repo_id: PydanticObjectId,
    stats: dict[str, int | dict[str, int]],
) -> None:
    """Compute and persist repository-level representations such as meta
    information based on the analysis statistics.

    `stats["files_detected"]` and `stats["files_by_extension"]` describe files
    detected, aggregated by the orchestrator from the per-batch
    activity outputs.

    Args:
        repo_id (PydanticObjectId): The repository ID.
        stats (dict[str, int | dict[str, int]]): Analysis statistics with keys
            `files_detected`, `files_by_extension`, `ast_success`,
            `ast_failed`, `cfg_success`, `cfg_failed`, `cfg_build_failed`,
            `dfg_success`, `dfg_failed`, `dfg_build_failed`, `doc_success`,
            `doc_failed`.
    """
    analysis_stats = AnalysisStats.model_validate(stats)
    content = create_repo_meta_summary(analysis_stats)
    await persist_repo_meta(repo_id, content, analysis_stats)


async def stream_extract_zip(blob_path: str) -> list[str]:
    """Extract a ZIP file from blob storage and upload every extracted file
    back to blob storage.

    Args:
        blob_path(str): The blob storage path of the ZIP file.

    Returns:
        list[str]: A list of blob storage paths for the extracted files.
    """

    logger.debug(f"Pipeline: Downloading ZIP blob '{blob_path}'")

    container = get_container_client()
    stream = await container.download_blob(blob_path)
    loop = asyncio.get_running_loop()

    # Derive the extracted prefix from the ZIP blob path
    extracted_prefix = blob_path.removesuffix(".zip")

    # Bounded queues for backpressure between stages
    chunk_q: queue.Queue[bytes | None] = queue.Queue(
        maxsize=pipeline_settings.ANALYZE_ZIP_CHUNK_Q_SIZE
    )
    file_q: asyncio.Queue[tuple[str, bytes] | None] = asyncio.Queue(
        maxsize=pipeline_settings.ANALYZE_ZIP_FILE_Q_SIZE
    )

    extract_future = loop.run_in_executor(
        None, stream_extract, chunk_q, file_q, loop, extracted_prefix
    )
    _, uploaded_paths = await asyncio.gather(
        stream_download(stream, chunk_q),
        stream_upload(file_q, container),
    )
    await extract_future

    logger.debug(
        f"Pipeline: Extracted {len(uploaded_paths)} file(s) from '{blob_path}'"
    )

    return uploaded_paths


async def get_source_code_from_blob(blob_path: str) -> str:
    """Get the source code content from a blob storage path.

    Args:
        blob_path(str): The blob storage path of the file.

    Returns:
        str: The content of the blob as a string.
    """
    container = get_container_client()
    stream = await container.download_blob(blob_path)
    raw = await stream.readall()
    source_code = raw.decode(errors="replace")

    return source_code


async def persist_pipeline_run(
    pipeline_run_id: PydanticObjectId,
    update_fields: dict[str, object],
    unset_fields: list[str] | None = None,
) -> PipelineRunDocument | None:
    """Persist updates to the PipelineRunDocument.

    Args:
        pipeline_run_id (PydanticObjectId): The ID of the PipelineRunDocument to
            update.
        update_fields (dict[str, object]): The fields to update with their new
            values.
        unset_fields (list[str] | None): Fields to remove from the document.

    Returns:
        PipelineRunDocument | None: The updated PipelineRunDocument, or None if not found.
    """
    update_expr: dict[str, object] = {"$set": update_fields}
    if unset_fields:
        update_expr["$unset"] = {f: "" for f in unset_fields}
    return await PipelineRunDocument.find_one(  # type: ignore
        PipelineRunDocument.id == pipeline_run_id
    ).update(update_expr)


async def update_pipeline_run(
    pipeline_run_id: PydanticObjectId,
    status: PipelineStatus | None = None,
    meta: PipelineMeta | None = None,
) -> None:
    """Update the PipelineRunDocument with the given status and meta information.

    Args:
        pipeline_run_id (PydanticObjectId): The ID of the PipelineRunDocument to update.
        status (PipelineStatus | None): The new status of the pipeline run, if updating.
        meta (PipelineMeta | None): Pipeline progress info (message + step).

    Raises:
        ValueError: If the PipelineRunDocument with the given ID is not found.
    """

    update_fields: dict[str, object] = {}
    unset_fields: list[str] | None = None

    if status is not None:
        update_fields["status"] = status.value
        if status in {PipelineStatus.COMPLETED, PipelineStatus.FAILED}:
            update_fields["finished_at"] = datetime.now(UTC)

    if status == PipelineStatus.COMPLETED:
        unset_fields = ["meta"]
    elif meta is not None:
        update_fields["meta.message"] = meta.message
        update_fields["meta.step"] = meta.step

    result = await mongodb_retry(
        persist_pipeline_run, pipeline_run_id, update_fields, unset_fields
    )
    if result is None:
        raise ValueError(f"PipelineRunDocument '{pipeline_run_id}' not found")

    if status is not None:
        log_level = "ERROR" if status == PipelineStatus.FAILED else "INFO"
        logger.log(log_level, f"Pipeline: '{pipeline_run_id}' {status.value}")
