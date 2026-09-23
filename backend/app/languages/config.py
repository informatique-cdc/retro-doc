"""Languages configuration.

This module defines the languages configuration.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class LanguagesSettings(BaseSettings):
    # Pydantic settings configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Language support
    LANGUAGES_CACHE_TTL_S: int = 300  # 5 minutes


languages_settings = LanguagesSettings()
