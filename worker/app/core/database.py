"""Database initialization.

This module initializes the MongoDB client and sets up the Beanie ODM with the
defined document models. After initialization, the client and database are
accessible via get_client() and get_database().

Since there is no lifespan management for Azure Durable Functions, the clients here
are initialized once but never closed. This is acceptable since they are designed
to be long-lived and the Azure Functions runtime will handle cleanup when the
function instance is recycled.
"""

import asyncio
import random
import sys
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from beanie import Document, init_beanie
from loguru import logger
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import BulkWriteError, OperationFailure

from app.core.config import settings
from app.docs.models import FileDocumentationDocument, RepoMetaDocument
from app.graphs.models import ASTDocument, CFGDocument, DFGDocument
from app.pipeline.models import PipelineRunDocument
from app.repos.models import FileDocument, RepoDocument

_T = TypeVar("_T")

_client: AsyncMongoClient[dict[str, object]] | None = None
_database: AsyncDatabase[dict[str, object]] | None = None
_write_sem = asyncio.Semaphore(settings.MONGODB_WRITE_CONCURRENCY)


def _get_retry_after_s(exc: Exception, attempt: int) -> float:
    """Get the retry delay in seconds from a MongoDB throttle exception.

    Args:
        exc (Exception): The exception from which to extract the retry delay.
        attempt (int): The current retry attempt number (0-based).

    Returns:
        float: The number of seconds to wait before retrying.
    """
    if isinstance(exc, OperationFailure) and exc.details:
        ms = exc.details.get("retryAfterMs") or exc.details.get("RetryAfterMs")
        if ms is not None:
            base = float(ms) / 1000
            return base * random.uniform(0.75, 1.25)  # nosec B311
    base = min(2.0**attempt, settings.MONGODB_RETRY_MAX_WAIT_S)
    return base * random.uniform(0.75, 1.25)  # nosec B311


async def _init_database_async() -> None:
    """Initialize MongoDB client and Beanie ODM.

    Creates the AsyncMongoClient, selects the database, and initializes
    Beanie with the registered document models.
    """
    logger.info("Core: Initializing MongoDB resources...")
    global _client, _database

    _client = AsyncMongoClient(settings.MONGODB_CONNECTION_STR.get_secret_value())
    _database = _client[settings.MONGODB_DB_NAME]

    doc_models: Sequence[type[Document]] = [
        RepoDocument,
        RepoMetaDocument,
        FileDocument,
        PipelineRunDocument,
        FileDocumentationDocument,
        ASTDocument,
        CFGDocument,
        DFGDocument,
    ]
    try:
        await init_beanie(database=_database, document_models=doc_models)
        logger.info("Core: MongoDB resources initialized.")
    except Exception:
        logger.exception("Core: MongoDB resources initialization failed.")
        sys.exit(1)


def _is_throttle_error(exc: Exception) -> bool:
    """Check if an exception is a MongoDB throttle error.

    Args:
        exc (Exception): The exception to check.

    Returns:
        bool: True if the exception is a throttle error, False otherwise.
    """
    if (
        isinstance(exc, OperationFailure)
        and exc.code in settings.MONGODB_THROTTLE_CODES
    ):
        return True
    if isinstance(exc, BulkWriteError):
        return any(
            e.get("code") in settings.MONGODB_THROTTLE_CODES
            for e in exc.details.get("writeErrors", [])
        )
    return False


def init_database() -> None:
    """Start database initialization as a background task.

    Schedules _init_database_async() on the running event loop.
    The Azure Functions runtime awaits all pending tasks before
    accepting requests. If initialization fails, the process exits.
    """
    asyncio.create_task(_init_database_async(), name="init_database_task")


async def mongodb_retry(
    fn: Callable[..., Awaitable[_T]], *args: object, **kwargs: object
) -> _T:
    """Execute an async DB operation with retry on MongoDB throttle.

    Operates at full speed and only backs off when the server returns
    throttle error code ("Too Many Requests"), using the server-provided
    headers when available and exponential backoff as a fallback.

    Args:
        fn(Callable[..., Awaitable[_T]]): The async function to execute.
        *args(object): Positional arguments to pass to the function.
        **kwargs(object): Keyword arguments to pass to the function.

    Returns:
        _T: The result of the function if successful.
    """
    last_exc: Exception | None = None
    max_attempts = settings.MONGODB_RETRY_MAX_ATTEMPTS
    for attempt in range(max_attempts):
        try:
            async with _write_sem:
                return await fn(*args, **kwargs)
        except (OperationFailure, BulkWriteError) as exc:
            if not _is_throttle_error(exc):
                raise
            last_exc = exc
            wait_s = _get_retry_after_s(exc, attempt)
            logger.warning(
                f"Core: MongoDB throttled, attempt {attempt + 1}/"
                f"{max_attempts}, retrying in {wait_s:.1f}s"
            )
            await asyncio.sleep(wait_s)
    raise last_exc


async def mongodb_retry_insert_many(
    doc_class: type[Document],
    docs: list[Document],
    chunk_size: int | None = None,
) -> tuple[int, int, list[int]]:
    """Insert documents in chunked sub-batches with per-document retry on throttle.

    Unlike `mongodb_retry(insert_many, ...)` which retries the entire batch
    (causing DuplicateKeyErrors for already-succeeded docs), this function:

    1. Splits *docs* into chunks of *chunk_size*.
    2. For each chunk, calls `insert_many(ordered=False)` under `_write_sem`.
    3. On `BulkWriteError`, separates throttled docs from dupes/other errors.
    4. Retries only the throttled docs across multiple rounds with backoff.

    Args:
        doc_class: The Beanie Document class (e.g. CFGDocument).
        docs: The list of document instances to insert.
        chunk_size: Override for `MONGODB_INSERT_CHUNK_SIZE`.

    Returns:
        `(succeeded, failed, error_codes)` where *error_codes* lists the
        distinct non-duplicate error codes encountered (for diagnostics).
    """
    if not docs:
        return (0, 0, [])

    if chunk_size is None:
        chunk_size = settings.MONGODB_INSERT_CHUNK_SIZE

    total_succeeded = 0
    permanent_failures = 0
    error_codes: set[int] = set()

    pending = list(docs)
    max_attempts = settings.MONGODB_RETRY_MAX_ATTEMPTS

    for attempt in range(max_attempts):
        if not pending:
            break

        throttled_docs: list[Document] = []
        max_retry_after_s: float = 0.0
        chunks = [
            pending[i : i + chunk_size] for i in range(0, len(pending), chunk_size)
        ]

        for chunk in chunks:
            try:
                async with _write_sem:
                    await doc_class.insert_many(chunk, ordered=False)
                total_succeeded += len(chunk)
            except BulkWriteError as exc:
                write_errors = exc.details.get("writeErrors", [])
                errored_indices: set[int] = set()

                # Extract server-provided retryAfterMs if available
                if exc.details:
                    ms = exc.details.get("retryAfterMs") or exc.details.get(
                        "RetryAfterMs"
                    )
                    if ms is not None:
                        max_retry_after_s = max(max_retry_after_s, float(ms) / 1000)

                for err in write_errors:
                    idx = err.get("index", -1)
                    code = err.get("code", -1)
                    errored_indices.add(idx)

                    if code == 11000:
                        # Duplicate key: already inserted (idempotent)
                        total_succeeded += 1
                    elif code in settings.MONGODB_THROTTLE_CODES:
                        # Throttled: collect for retry
                        if 0 <= idx < len(chunk):
                            throttled_docs.append(chunk[idx])
                    else:
                        # Permanent failure
                        permanent_failures += 1
                        error_codes.add(code)

                # Docs NOT in errored_indices succeeded
                total_succeeded += len(chunk) - len(errored_indices)

        pending = throttled_docs

        if pending and attempt < max_attempts - 1:
            # Prefer server-provided retry delay, fall back to exponential
            wait_s = max_retry_after_s or min(
                2.0**attempt, settings.MONGODB_RETRY_MAX_WAIT_S
            )
            wait_s *= random.uniform(0.75, 1.25)  # nosec B311
            logger.warning(
                f"Core: MongoDB {len(pending)} doc(s) throttled (insert many), "
                f"attempt {attempt + 1}/{max_attempts}, retrying in {wait_s:.1f}s"
            )
            await asyncio.sleep(wait_s)

    # Remaining pending docs after all attempts are permanent failures
    if pending:
        permanent_failures += len(pending)
        for code in settings.MONGODB_THROTTLE_CODES:
            error_codes.add(code)
        logger.warning(
            f"Core: MongoDB {len(pending)} doc(s) still throttled (insert many) "
            f"after {max_attempts} attempts"
        )

    return (total_succeeded, permanent_failures, sorted(error_codes))
