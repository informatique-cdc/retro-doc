"""Auth configuration.

This module defines the auth configuration.
"""

from pydantic import SecretStr
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
    OIDC_JWKS_CACHE_TTL_S: int = 300  # 5 minutes

    # Internal app-issued JWT
    JWT_AUDIENCE: str = "retro-doc-backend"
    JWT_ISSUER: str = "retro-doc-backend"
    JWT_SECRET: SecretStr
    JWT_ACCESS_TOKEN_DURATION_S: int = 900  # 15 minutes
    JWT_REFRESH_TOKEN_DURATION_S: int = 1209600  # 14 days


auth_settings = AuthSettings()
