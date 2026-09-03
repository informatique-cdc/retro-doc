"""Deep analysis configuration.

This module defines the deep analysis configuration.
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DeepAnalysisSettings(BaseSettings):
    # Pydantic settings configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Deep analysis model
    DEEP_ANALYSIS_MODEL_BASE_URL: str | None = None
    DEEP_ANALYSIS_MODEL_API_KEY: SecretStr | None = None
    DEEP_ANALYSIS_MODEL_NAME: str | None = None
    DEEP_ANALYSIS_MODEL_PROVIDER: str | None = None
    DEEP_ANALYSIS_MODEL_TEMPERATURE: float | None = 0

    DEEP_ANALYSIS_FALLBACK_MODEL_BASE_URL: str | None = None
    DEEP_ANALYSIS_FALLBACK_MODEL_API_KEY: SecretStr | None = None
    DEEP_ANALYSIS_FALLBACK_MODEL_NAME: str | None = None
    DEEP_ANALYSIS_FALLBACK_MODEL_PROVIDER: str | None = None
    DEEP_ANALYSIS_FALLBACK_MODEL_TEMPERATURE: float | None = None

    # Agent
    DEEP_AGENT_MODEL_CALL_LIMIT: int = 120
    DEEP_AGENT_TASK_CALL_LIMIT: int = 3
    DEEP_AGENT_TOOL_CALL_LIMIT: int = 100

    DEEP_AGENT_SUBAGENT_MODEL_CALL_LIMIT: int = 60
    DEEP_AGENT_SUBAGENT_TOOL_CALL_LIMIT: int = 50

    DEEP_AGENT_PROGRESS_FLUSH_INTERVAL_S: float = 1.0
    DEEP_AGENT_STALE_THRESHOLD_S: float = 450.0

    # Gotenberg
    GOTENBERG_BASE_URL: str

    # API response
    QUERY_PREVIEW_MAX_LENGTH: int = 50


deep_analysis_settings = DeepAnalysisSettings()
