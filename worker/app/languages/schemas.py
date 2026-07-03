"""Languages schemas.

This module defines the schemas for the languages endpoint (e.g., Data Transfer Object - DTO).
"""

from pydantic import BaseModel

from app.core.language import Language


class LanguagesResponse(BaseModel):
    languages: list[Language]
