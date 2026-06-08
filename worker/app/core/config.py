"""Global configuration.

This module defines the global configuration for the Retro-Documentation Backend application.
"""

from datetime import timezone
from typing import Literal

from azure.functions import AuthLevel
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
    APP_AUTH_LEVEL: AuthLevel = AuthLevel.ANONYMOUS
    APP_TIMEZONE: timezone = timezone.utc

    # Logging
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "DEBUG"
    LOG_DIAGNOSE: bool = False

    # MongoDB
    MONGODB_CONNECTION_STR: SecretStr
    MONGODB_DB_NAME: str = "retro-doc-backend"
    MONGODB_INSERT_CHUNK_SIZE: int = 5
    MONGODB_RETRY_MAX_ATTEMPTS: int = 8
    MONGODB_RETRY_MAX_WAIT_S: float = 32.0
    MONGODB_THROTTLE_CODES: list[int] = [429, 16500]
    MONGODB_WRITE_CONCURRENCY: int = 3

    # Blob Storage
    BLOB_STORAGE_ACCOUNT_URL: str
    BLOB_STORAGE_CONTAINER_NAME: str = "retro-doc-backend"


settings = Settings()
