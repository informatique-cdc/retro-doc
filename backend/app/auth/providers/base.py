"""Base class for identity providers.

This module defines the provider-agnostic contract that every identity provider
must implement, along with the normalized identity returned after a credential
is validated. New providers (Google, GitHub, ...) implement `AuthProvider` and
register themselves in `app.auth.providers.registry`.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class ProviderIdentity(BaseModel):
    """Normalized identity extracted from a validated provider credential.

    The fields mirror the claims `app.auth.schemas.User` needs so that the
    derived `uid` stays stable across providers and over time.
    """

    iss: str = Field(min_length=1)
    sub: str = Field(min_length=1)
    oid: str | None = None  # Azure specific
    tid: str | None = None  # Azure specific
    name: str | None = None
    preferred_username: str | None = None


class AuthProvider(ABC):
    """Contract for an external identity provider used at login."""

    @abstractmethod
    async def authenticate(self, credential: str) -> ProviderIdentity:
        """Validate a provider credential and return a normalized identity.

        Args:
            credential (str): The provider-issued credential to validate
                (for OIDC providers, an id_token).

        Returns:
            ProviderIdentity: The normalized identity of the authenticated user.

        Raises:
            jwt.exceptions.InvalidTokenError: If the credential is invalid.
        """
        ...
