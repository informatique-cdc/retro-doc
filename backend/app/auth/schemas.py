"""Auth schemas.

This module defines the data models (schemas) used for authentication-related operations.
"""

import hashlib
from typing import Self

from pydantic import BaseModel, model_validator


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
        if self.oid and self.tid:
            raw = f"azure:{self.tid}:{self.oid}"
        else:
            raw = f"oidc:{self.iss}:{self.sub}"
        self.uid = hashlib.sha256(raw.encode()).hexdigest()
        return self
