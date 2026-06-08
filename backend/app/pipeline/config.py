"""Pipeline configuration.

This module defines the pipeline configuration for the Retro-Documentation Backend application.
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


pipeline_settings = PipelineSettings()
