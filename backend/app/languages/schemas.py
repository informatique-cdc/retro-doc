"""Languages Pydantic schemas.

This module defines the schemas for the languages endpoints (e.g., Data Transfer Object - DTO).
"""

from pydantic import BaseModel


class SupportedLanguagesResponse(BaseModel):
    languages: list[str]
