"""Chat vectorstore.

This module manages the Azure AI Search VectorStore lifecycle for RAG retrieval.
"""

from azure.search.documents.indexes.models import (
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SimpleField,
)
from langchain.embeddings import init_embeddings
from langchain_azure_ai.vectorstores import AzureSearch
from loguru import logger

from app.chat.config import chat_settings

_vectorstore: AzureSearch | None = None


def _get_index_fields() -> list[SearchField]:
    """Return the Azure AI Search index field schema.

    Includes a top-level filterable `repo_id` field. This is required
    because metadata is stored as a JSON string and is not filterable
    by itself.

    Returns:
        list[SearchField]: The list of index
            fields for Azure AI Search.
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
            vector_search_dimensions=chat_settings.EMBEDDING_MODEL_DIMENSIONS,
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


def init_vectorstore() -> None:
    """Initialize the Azure AI Search VectorStore.

    Creates the embedding function via `init_embeddings` (provider-agnostic)
    and the `AzureSearch` VectorStore with custom filterable fields.
    """
    global _vectorstore

    logger.info("Chat: Initializing VectorStore resources...")

    embeddings = init_embeddings(
        model=chat_settings.EMBEDDING_MODEL_NAME,
        provider=chat_settings.EMBEDDING_MODEL_PROVIDER,
        api_key=chat_settings.EMBEDDING_MODEL_API_KEY.get_secret_value(),
        azure_endpoint=chat_settings.EMBEDDING_MODEL_ENDPOINT,
    )

    _vectorstore = AzureSearch(
        azure_search_endpoint=chat_settings.AZURE_AI_SEARCH_ENDPOINT,
        azure_search_key=chat_settings.AZURE_AI_SEARCH_API_KEY.get_secret_value(),
        index_name=chat_settings.AZURE_AI_SEARCH_INDEX_NAME,
        embedding_function=embeddings,
        semantic_configuration_name="mySemanticConfig",
        fields=_get_index_fields(),
        additional_search_client_options={"retry_total": 4},
    )

    logger.info("Chat: VectorStore resources initialized.")


async def close_vectorstore() -> None:
    """Close the async search client."""
    global _vectorstore

    logger.info("Chat: Closing VectorStore resources...")

    if _vectorstore is not None:
        await _vectorstore.async_client.close()

    _vectorstore = None

    logger.info("Chat: VectorStore resources closed.")


def get_vectorstore() -> AzureSearch:
    """Get the initialized VectorStore.

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
