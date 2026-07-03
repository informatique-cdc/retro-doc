"""Pipeline configuration.

This module defines the pipeline configuration.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineSettings(BaseSettings):
    # Pydantic settings configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Azure Durable Functions
    DURABLE_FUNCTIONS_BASE_URL: str

    # Language support
    LANGUAGES_CACHE_TTL_S: int = 300  # 5 minutes


pipeline_settings = PipelineSettings()
