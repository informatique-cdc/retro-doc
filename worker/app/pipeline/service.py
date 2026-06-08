"""Pipeline service.

This module defines the service layer for the pipeline-related operations.
"""

import asyncio
import posixpath
import queue
from datetime import datetime
from typing import Any

from beanie import PydanticObjectId
from loguru import logger

from app.core.blob_storage import get_container_client
from app.core.config import settings
from app.core.database import mongodb_retry
from app.core.language_enum import Language, LanguageExtension
from app.docs.models import FileDocumentationDocument
from app.docs.service import (
    create_meta_repo,
    generate_documentation_file,
    persist_documentation,
    persist_meta_repo,
)
from app.graphs.models import CFGDocument, DFGDocument
from app.graphs.service import (
    get_graph_services,
    persist_ast,
    persist_scoped_graphs,
)
from app.pipeline.config import pipeline_settings
from app.pipeline.models import PipelineRunDocument, PipelineStatus
from app.pipeline.utils import stream_download, stream_extract, stream_upload
from app.rag.service import index_file_documentation
from app.repos.service import persist_file


async def compute_file_representations(
    blob_path: str,
    repo_id: PydanticObjectId,
    language: Language,
) -> dict[str, str | int]:
    """Create a file document, download the source once, build all graphs
    and documentation independently, and persist results.

    Each graph type (AST, CFG, DFG) is built independently so a failure
    in one does not block the others.

    Args:
        blob_path(str): The blob storage path of the source file.
        repo_id(PydanticObjectId): The repository ID.
        language(Language): The programming language.

    Returns:
        dict: `{"file_id": str, "ast": int, "cfg_succeeded": int, "cfg_failed": int,
            "dfg_succeeded": int, "dfg_failed": int, "doc": int}`.
            `*_succeeded`/`*_failed` count graph documents.
            `*_failed == -1` signals the upstream builder returned None.
    """
    relative_path = posixpath.join(*blob_path.split("/")[5:])

    # Step 1: Create FileDocument
    file_id = await persist_file(repo_id, relative_path, file_hash="")

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
        logger.exception(f"Analyze File: AST parse failed for '{blob_path}'")

    try:
        cfg_results = cfg_builder.build(source_code, relative_path)
    except Exception:
        logger.exception(f"Analyze File: CFG build failed for '{blob_path}'")

    try:
        dfg_results = dfg_builder.build(source_code, relative_path)
    except Exception:
        logger.exception(f"Analyze File: DFG build failed for '{blob_path}'")

    # Step 4: Persist all graphs concurrently
    ast_count, (cfg_ok, cfg_err), (dfg_ok, dfg_err) = await asyncio.gather(
        persist_ast(ast_data, repo_id, file_id, blob_path),
        persist_scoped_graphs(cfg_results, CFGDocument, repo_id, file_id, blob_path),
        persist_scoped_graphs(dfg_results, DFGDocument, repo_id, file_id, blob_path),
    )

    # Step 5: Generate documentation via LLM
    doc_count = -1
    doc: str | None = None
    existing_doc: FileDocumentationDocument | None = None
    try:
        existing_doc = await FileDocumentationDocument.find_one(
            FileDocumentationDocument.repo_id == repo_id,
            FileDocumentationDocument.file_id == file_id,
        )
        if not existing_doc:
            doc = await generate_documentation_file(
                relative_path,
                source_code,
                ast_data,
                cfg_results,
                dfg_results,
                language,
            )
            await persist_documentation(repo_id, file_id, doc)
        doc_count = 1
    except Exception:
        logger.exception(
            f"Analyze File: Documentation generation failed for '{blob_path}'"
        )

    # Step 6: Index documentation in vectorstore for RAG
    rag_count = -1
    try:
        doc_content = doc if doc else (existing_doc.content if existing_doc else None)
        if doc_content:
            rag_count = await index_file_documentation(
                repo_id=str(repo_id),
                file_id=str(file_id),
                file_path=relative_path,
                content=doc_content,
            )
    except Exception:
        logger.exception(f"Analyze File: RAG indexing failed for '{blob_path}'")

    logger.debug(
        f"Analyze File: File '{file_id}' - AST={ast_count}, CFG=({cfg_ok}/{cfg_err}), DFG=({dfg_ok}/{dfg_err}), Doc={doc_count}, RAG={rag_count}"
    )

    return {
        "file_id": str(file_id),
        "ast": ast_count,
        "cfg_succeeded": cfg_ok,
        "cfg_failed": cfg_err,
        "dfg_succeeded": dfg_ok,
        "dfg_failed": dfg_err,
        "doc": doc_count,
        "rag": rag_count,
    }


async def compute_repo_representations(
    repo_id: PydanticObjectId,
    stats: dict[str, int],
) -> None:
    """Compute and persist repository-level representations such as meta
    information based on the analysis statistics.

    Args:
        repo_id (PydanticObjectId): The repository ID.
        stats (dict[str, int]): Analysis statistics with keys
            `total_files`, `ast_success`, `ast_failed`, `cfg_success`,
            `cfg_failed`, `cfg_build_failed`, `dfg_success`, `dfg_failed`,
            `dfg_build_failed`, `doc_success`, `doc_failed`.
    """
    meta = create_meta_repo(stats)
    await persist_meta_repo(repo_id, meta)


async def stream_extract_zip(blob_path: str, language: Language) -> list[str]:
    """Extract a ZIP file from blob storage and upload extracted files back to blob storage.

    Args:
        blob_path(str): The blob storage path of the ZIP file.
        language(Language): The programming language of the files in the ZIP.

    Returns:
        list[str]: A list of blob storage paths for the extracted files.
    """

    logger.debug(f"Stream Extract ZIP: Downloading ZIP blob '{blob_path}'")

    extension = LanguageExtension[language.name].value
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
        None, stream_extract, chunk_q, file_q, loop, extension, extracted_prefix
    )
    _, uploaded_paths = await asyncio.gather(
        stream_download(stream, chunk_q),
        stream_upload(file_q, container),
    )
    await extract_future

    logger.debug(
        f"Stream Extract ZIP: Extracted {len(uploaded_paths)} {language.value} files from '{blob_path}'"
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
    pipeline_run_id: PydanticObjectId, update_fields: dict[str, object]
) -> PipelineRunDocument | None:
    """Persist updates to the PipelineRunDocument.

    Args:
        pipeline_run_id (PydanticObjectId): The ID of the PipelineRunDocument to
            update.
        update_fields (dict[str, object]): The fields to update with their new
            values.

    Returns:
        PipelineRunDocument | None: The updated PipelineRunDocument, or None if not found.
    """
    return await PipelineRunDocument.find_one(  # type: ignore
        PipelineRunDocument.id == pipeline_run_id
    ).update({"$set": update_fields})


async def update_pipeline_run(
    pipeline_run_id: PydanticObjectId,
    status: PipelineStatus | None = None,
    meta: str | None = None,
) -> None:
    """Update the PipelineRunDocument with the given status and meta information.

    Args:
        pipeline_run_id (PydanticObjectId): The ID of the PipelineRunDocument to update.
        status (PipelineStatus | None): The new status of the pipeline run, if updating.
        meta (str | None): Additional metadata or error information, if updating.

    Raises:
        ValueError: If the PipelineRunDocument with the given ID is not found.
    """

    update_fields: dict[str, object] = {}

    if status is not None:
        update_fields["status"] = status.value
        if status in {PipelineStatus.COMPLETED, PipelineStatus.FAILED}:
            update_fields["finished_at"] = datetime.now(settings.APP_TIMEZONE)
    if meta is not None:
        update_fields["meta"] = meta

    result = await mongodb_retry(persist_pipeline_run, pipeline_run_id, update_fields)
    if result is None:
        raise ValueError(f"Update Pipeline Run Doc: '{pipeline_run_id}' not found")

    if status is not None:
        base_log = (
            f"Update Pipeline Run Doc: Pipeline '{pipeline_run_id}' {status.value}"
        )
        log_level = "ERROR" if status == PipelineStatus.FAILED else "INFO"
        logger.log(
            log_level,
            f"{base_log} with error '{meta}'"
            if status == PipelineStatus.FAILED
            else base_log,
        )
