"""Pipeline configuration.

This module defines the pipeline configuration for the Retro-Documentation Backend Worker application.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineSettings(BaseSettings):
    # Pydantic settings configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Orchestrator / activity
    ANALYZE_BATCH_SIZE: int = 10
    ANALYZE_FILE_CONCURRENCY: int = 3
    ANALYZE_RETRY_ATTEMPTS: int = 3
    ANALYZE_RETRY_INTERVAL_MS: int = 2000
    ANALYZE_ZIP_CHUNK_Q_SIZE: int = 16
    ANALYZE_ZIP_FILE_Q_SIZE: int = 4


pipeline_settings = PipelineSettings()
