"""Documentation configuration.

This module defines the documentation configuration for the Retro-Documentation Backend Worker application.
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DocSettings(BaseSettings):
    # Pydantic settings configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Chat model
    CHAT_BASE_URL: str
    CHAT_API_KEY: SecretStr
    CHAT_NAME: str = "Codestral-2501"
    CHAT_PROVIDER: str = "mistralai"
    CHAT_TEMPERATURE: float | None = 0.1

    # Prompt
    PROMPT_MAX_SOURCE_CHARS: int = 10_000
    PROMPT_MAX_GRAPH_CHARS: int = 10_000


docs_settings = DocSettings()
