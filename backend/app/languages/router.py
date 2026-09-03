"""Languages router.

This module defines the API endpoints related to supported languages.
"""

from fastapi import APIRouter

from app.languages.schemas import SupportedLanguagesResponse
from app.languages.service import get_supported_languages

languages_router = APIRouter(prefix="/languages", tags=["languages"])


@languages_router.get("", response_model=SupportedLanguagesResponse)
async def get_languages_endpoint() -> SupportedLanguagesResponse:
    """Get the programming languages supported by the analysis worker.

    Returns:
        SupportedLanguagesResponse: The list of supported language codes.
    """
    languages = await get_supported_languages()

    return SupportedLanguagesResponse(languages=languages)
