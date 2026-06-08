"""RAG configuration.

This module defines the RAG configuration for the Retro-Documentation Backend Worker application.
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RAGSettings(BaseSettings):
    # Pydantic settings configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Azure AI Search
    AZURE_AI_SEARCH_ENDPOINT: str
    AZURE_AI_SEARCH_API_KEY: SecretStr
    AZURE_AI_SEARCH_INDEX_NAME: str = "retro-doc"
    AZURE_AI_SEARCH_RETRY_MAX_ATTEMPTS: int = 5
    AZURE_AI_SEARCH_RETRY_MAX_WAIT_S: float = 32.0
    AZURE_AI_SEARCH_THROTTLE_CODES: list[int] = [429, 503]
    AZURE_AI_SEARCH_WRITE_CONCURRENCY: int = 5

    # Embedding model
    EMBEDDING_ENDPOINT: str
    EMBEDDING_API_KEY: SecretStr
    EMBEDDING_NAME: str = "text-embedding-3-small"
    EMBEDDING_PROVIDER: str = "azure_openai"
    EMBEDDING_DIMENSIONS: int = 1536

    # Chunking
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50


rag_settings = RAGSettings()
