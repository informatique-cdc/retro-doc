"""Graphs persistence service.

This module defines persistence functions for graph documents (AST, CFG, DFG).
"""

from typing import Any

from beanie import PydanticObjectId
from loguru import logger
from pymongo.errors import DuplicateKeyError

from app.core.database import mongodb_retry, mongodb_retry_insert_many
from app.graphs.models import ASTDocument, CFGDocument, DFGDocument


async def persist_ast(
    ast_data: dict[str, Any] | None,
    repo_id: PydanticObjectId,
    file_id: PydanticObjectId,
    blob_path: str,
) -> int:
    """Persist AST document to MongoDB.

    Args:
        ast_data (dict[str, Any] | None): The AST data to persist.
        repo_id (PydanticObjectId): The repository ID.
        file_id (PydanticObjectId): The file ID.
        blob_path (str): The blob path of the source file (for logging).

    Returns:
        int: 1 on success, -1 on failure.
    """
    try:
        if ast_data is None:
            logger.debug(
                f"Persist AST: AST parse returned None for '{blob_path}' [SKIP]"
            )
            return -1
        ast_doc = ASTDocument(repo_id=repo_id, file_id=file_id, content=ast_data)
        try:
            await mongodb_retry(ast_doc.insert)
        except DuplicateKeyError:
            pass
        return 1
    except Exception:
        logger.exception(f"Persist AST: AST failed for '{blob_path}'")
        return -1


async def persist_scoped_graphs(
    graph_data: list[dict[str, Any]] | None,
    doc_class: type[CFGDocument] | type[DFGDocument],
    repo_id: PydanticObjectId,
    file_id: PydanticObjectId,
    blob_path: str,
) -> tuple[int, int]:
    """Persist scoped graph documents (CFG or DFG) to MongoDB.

    Args:
        graph_data(list[dict[str, Any]] | None): The list of graphs to persist.
        doc_class(type[CFGDocument] | type[DFGDocument]): The document class.
        repo_id(PydanticObjectId): The repository ID.
        file_id(PydanticObjectId): The file ID.
        blob_path(str): The blob path of the source file (for logging).

    Returns:
        tuple[int, int]: `(succeeded, failed)` counts of graph documents.
            Returns `(0, -1)` when `graph_data` is `None` (builder failed).
    """
    label = doc_class.__name__.removesuffix("Document")
    try:
        if graph_data is None:
            logger.debug(
                f"Persist {label}s: {label} build returned None for '{blob_path}' [SKIP]"
            )
            return (0, -1)
        docs = []
        for item in graph_data:
            item = item.copy()
            scope = item.pop("scope", None)
            docs.append(
                doc_class(
                    repo_id=repo_id,
                    file_id=file_id,
                    content=item,
                    scope=scope,
                )
            )
        if docs:
            succeeded, failed, error_codes = await mongodb_retry_insert_many(
                doc_class, docs
            )
            if failed > 0:
                logger.warning(
                    f"Persist {label}s: {failed} write error(s) "
                    f"(codes: {error_codes}) for '{blob_path}'"
                )
            return (succeeded, failed)
        return (len(graph_data), 0)
    except Exception:
        logger.exception(f"Persist {label}s: {label} failed for '{blob_path}'")
        return (0, len(graph_data))  # type: ignore
