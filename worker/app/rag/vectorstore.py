"""RAG vectorstore.

This module manages the Azure AI Search VectorStore lifecycle for RAG retrieval.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from azure.core.exceptions import HttpResponseError
from azure.search.documents.indexes.models import (
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SimpleField,
)
from langchain.embeddings import init_embeddings
from langchain_azure_ai.vectorstores import AzureSearch
from loguru import logger

from app.rag.config import rag_settings

_T = TypeVar("_T")

_vectorstore: AzureSearch | None = None
_write_sem = asyncio.Semaphore(rag_settings.AZURE_AI_SEARCH_WRITE_CONCURRENCY)


def _get_index_fields() -> list[SearchField]:
    """Return the Azure AI Search index field schema.

    Includes a top-level filterable `repo_id` field. This is required
    because metadata is stored as a JSON string and is not filterable by
    itself.

    Returns:
        list[SearchField]: The list of index fields for Azure AI Search.
    """
    return [
        SimpleField(
            name="id",
            type=SearchFieldDataType.String,
            key=True,
            filterable=True,
        ),
        SearchableField(
            name="content",
            type=SearchFieldDataType.String,
        ),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=rag_settings.EMBEDDING_DIMENSIONS,
            vector_search_profile_name="myHnswProfile",
        ),
        SearchableField(
            name="metadata",
            type=SearchFieldDataType.String,
        ),
        SimpleField(
            name="repo_id",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
    ]


def _get_retry_after_s(exc: HttpResponseError, attempt: int) -> float:
    """Get the retry delay in seconds from an Azure AI Search throttle error response.

    Inspects the `Retry-After` and `retry-after-ms` response headers.
    Falls back to exponential backoff when the headers are absent.

    Args:
        exc(HttpResponseError): The throttle exception.
        attempt(int): The current retry attempt number (0-based).

    Returns:
        float: The number of seconds to wait before retrying.
    """
    if exc.response is not None:
        headers = getattr(exc.response, "headers", {})
        retry_after = headers.get("Retry-After")
        if retry_after is not None:
            return float(retry_after)
        retry_after_ms = headers.get("retry-after-ms")
        if retry_after_ms is not None:
            return float(retry_after_ms) / 1000
    return min(2.0**attempt, rag_settings.AZURE_AI_SEARCH_RETRY_MAX_WAIT_S)


def _is_throttle_error(exc: Exception) -> bool:
    """Check if an exception is an Azure AI Search throttle error.

    Args:
        exc (Exception): The exception to check.

    Returns:
        bool: True if the exception is a throttle error, False otherwise.
    """
    return (
        isinstance(exc, HttpResponseError)
        and exc.status_code in rag_settings.AZURE_AI_SEARCH_THROTTLE_CODES
    )


async def azure_ai_search_retry(
    fn: Callable[..., Awaitable[_T]], *args: object, **kwargs: object
) -> _T:
    """Execute an async Azure AI Search operation with retry on throttle.

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
    max_attempts = rag_settings.AZURE_AI_SEARCH_RETRY_MAX_ATTEMPTS
    for attempt in range(max_attempts):
        try:
            async with _write_sem:
                return await fn(*args, **kwargs)
        except HttpResponseError as exc:
            if not _is_throttle_error(exc):
                raise
            last_exc = exc
            wait_s = _get_retry_after_s(exc, attempt)
            logger.warning(
                f"Azure AI Search throttled ({exc.status_code}), "
                f"attempt {attempt + 1}/{max_attempts}, "
                f"retrying in {wait_s:.1f}s"
            )
            await asyncio.sleep(wait_s)
    raise last_exc


def init_vectorstore() -> None:
    """Initialize the Azure AI Search VectorStore.

    Creates the embedding function via `init_embeddings` (provider-agnostic)
    and the `AzureSearch` VectorStore with custom filterable fields.
    """
    global _vectorstore

    logger.info("RAG: Initializing VectorStore resources...")

    embeddings = init_embeddings(
        model=rag_settings.EMBEDDING_NAME,
        provider=rag_settings.EMBEDDING_PROVIDER,
        api_key=rag_settings.EMBEDDING_API_KEY.get_secret_value(),
        azure_endpoint=rag_settings.EMBEDDING_ENDPOINT,
    )

    _vectorstore = AzureSearch(
        azure_search_endpoint=rag_settings.AZURE_AI_SEARCH_ENDPOINT,
        azure_search_key=rag_settings.AZURE_AI_SEARCH_API_KEY.get_secret_value(),
        index_name=rag_settings.AZURE_AI_SEARCH_INDEX_NAME,
        embedding_function=embeddings,
        semantic_configuration_name="mySemanticConfig",
        fields=_get_index_fields(),
        additional_search_client_options={"retry_total": 4},
    )

    logger.info("RAG: VectorStore resources initialized.")


def get_vectorstore() -> AzureSearch:
    """Get the initialized vectorstore.

    Raises:
        RuntimeError: If `init_vectorstore()` has not been called yet.

    Returns:
        AzureSearch: The initialized VectorStore instance.
    """
    if _vectorstore is None:
        raise RuntimeError(
            "VectorStore not initialized. Call init_vectorstore() first."
        )
    return _vectorstore
