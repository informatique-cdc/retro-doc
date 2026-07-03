"""Chat configuration.

This module defines the chat configuration.
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChatSettings(BaseSettings):
    # Pydantic settings configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Chat model
    CHAT_MODEL_BASE_URL: str
    CHAT_MODEL_API_KEY: SecretStr
    CHAT_MODEL_NAME: str = "Mistral-Large-3"
    CHAT_MODEL_PROVIDER: str = "mistralai"
    CHAT_MODEL_TEMPERATURE: float | None = 0.1

    CHAT_FALLBACK_MODEL_BASE_URL: str | None = None
    CHAT_FALLBACK_MODEL_API_KEY: SecretStr | None = None
    CHAT_FALLBACK_MODEL_NAME: str | None = None
    CHAT_FALLBACK_MODEL_PROVIDER: str | None = None
    CHAT_FALLBACK_MODEL_TEMPERATURE: float | None = None

    # Summarization model
    SUMMARIZATION_MODEL_BASE_URL: str | None = None
    SUMMARIZATION_MODEL_API_KEY: SecretStr | None = None
    SUMMARIZATION_MODEL_NAME: str | None = None
    SUMMARIZATION_MODEL_PROVIDER: str | None = None
    SUMMARIZATION_MODEL_TEMPERATURE: float | None = None

    # Title generation model
    TITLE_MAX_LEN: int = 50
    TITLE_MODEL_BASE_URL: str | None = None
    TITLE_MODEL_API_KEY: SecretStr | None = None
    TITLE_MODEL_NAME: str | None = None
    TITLE_MODEL_PROVIDER: str | None = None
    TITLE_MODEL_TEMPERATURE: float | None = None

    # Agent
    AGENT_MODEL_CALL_LIMIT: int = 25
    AGENT_RECURSION_LIMIT: int = 128  # Safety net. AGENT_MODEL_CALL_LIMIT and AGENT_TOOL_CALL_LIMIT are the primary budgets.
    AGENT_TOOL_CALL_LIMIT: int = 15

    AGENT_SUMMARIZATION_TRIGGER: tuple[str, int] | None = ("tokens", 80_000)
    AGENT_SUMMARIZATION_KEEP: tuple[str, int] | None = ("tokens", 20_000)

    # Agent checkpointer
    MONGODB_SOCKET_TIMEOUT_MS: int = 90_000
    MONGODB_CONNECT_TIMEOUT_MS: int = 10_000
    MONGODB_SERVER_SELECTION_TIMEOUT_MS: int = 10_000
    MONGODB_MAX_IDLE_TIME_MS: int = 10_000

    # Agent tools
    REPO_GLOB_MAX_RESULTS: int = 100
    REPO_READ_FILE_GRAPH_MAX_CONTENT_LENGTH: int = 10_000
    REPO_READ_FILE_MAX_CONTENT_LENGTH: int = 10_000
    REPO_SEARCH_DOCS_TOP_K: int = 5
    RETRIEVE_MESSAGES_MAX_RESULTS: int = 50
    RETRIEVE_MESSAGES_MAX_CONTENT_LENGTH: int = 500

    # Azure AI Search
    AZURE_AI_SEARCH_ENDPOINT: str
    AZURE_AI_SEARCH_API_KEY: SecretStr
    AZURE_AI_SEARCH_INDEX_NAME: str = "retro-doc"

    # Embedding model
    EMBEDDING_MODEL_ENDPOINT: str
    EMBEDDING_MODEL_API_KEY: SecretStr
    EMBEDDING_MODEL_NAME: str = "text-embedding-3-small"
    EMBEDDING_MODEL_PROVIDER: str = "azure_openai"
    EMBEDDING_MODEL_DIMENSIONS: int = 1536


chat_settings = ChatSettings()
