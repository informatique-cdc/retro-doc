"""Version Pydantic schemas.

This modules defines the schemas for the version endpoint (e.g., Data Transfer Object - DTO).
"""

from pydantic import BaseModel


class VersionResponse(BaseModel):
    version: str
