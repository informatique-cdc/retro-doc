"""Global configuration.

This module defines the global configuration for the Retro-Documentation Backend application.
"""

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Pydantic settings configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General
    APP_DEBUG: bool = False
    APP_NAME: str = "Retro-Documentation Backend API"
    APP_VERSION: str = "0.1.0"
    APP_ROOT_PATH: str = "/api/v0"
    APP_CORS_ORIGINS: list[str] = ["*"]

    # Logging
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "DEBUG"
    LOG_DIAGNOSE: bool = False

    # MongoDB
    MONGODB_CONNECTION_STR: SecretStr
    MONGODB_DB_NAME: str = "retro-doc-backend"

    # Blob Storage
    BLOB_STORAGE_ACCOUNT_URL: str
    BLOB_STORAGE_CONTAINER_NAME: str = "retro-doc-backend"


settings = Settings()
