"""Auth configuration.

This module defines the auth configuration for the Retro-Documentation Backend application.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    # Pydantic settings configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General
    APP_AUTH_DEBUG: bool = False

    # OpenID Connect (OIDC)
    OIDC_AUDIENCE: str
    OIDC_ALLOWED_ISSUERS: list[str]
    OIDC_JWKS_URL: str
    OIDC_JWKS_CACHE_TTL: int = 300  # 5 minutes


auth_settings = AuthSettings()
