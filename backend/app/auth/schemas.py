"""Auth schemas.

This module defines the data models (schemas) used for authentication-related operations.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.auth.utils import compute_uid


class AuthProviderName(StrEnum):
    """Supported external identity providers for login."""

    MICROSOFT = "microsoft"


class User(BaseModel):
    iss: str
    sub: str
    aud: str
    exp: int
    nbf: int
    iat: int
    name: str | None = None
    preferred_username: str | None = None
    oid: str | None = None  # Azure specific
    tid: str | None = None  # Azure specific
    uid: str | None = None  # Computed unique user ID

    @model_validator(mode="after")
    def _compute_uid(self) -> Self:
        # An already-set uid came from a signature-verified app token; keep it.
        if not self.uid:
            self.uid = compute_uid(
                oid=self.oid, tid=self.tid, iss=self.iss, sub=self.sub
            )
        return self


class LoginRequest(BaseModel):
    token: str = Field(min_length=1)


class MeResponse(BaseModel):
    uid: str
    name: str | None = None
    preferred_username: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenClaims(BaseModel):
    """Identity claims embedded in app-issued tokens."""

    uid: str
    sub: str
    oid: str | None = None
    tid: str | None = None
    name: str | None = None
    preferred_username: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token lifetime in seconds
