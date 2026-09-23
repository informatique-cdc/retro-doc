"""Languages service.

This module provides a cached client to fetch the programming languages
supported by the analysis worker (Azure Durable Functions app).
"""

import asyncio
import time
from typing import Any

import httpx
from fastapi import HTTPException, status
from loguru import logger

from app.languages.config import languages_settings
from app.pipeline.config import pipeline_settings

# Simple in-memory cache for the supported languages with expiration
_languages_cache: dict[str, Any] = {"languages": None, "expires_at": 0}
_languages_lock = asyncio.Lock()


async def get_supported_languages(force: bool = False) -> list[str]:
    """Fetch the languages the analysis worker supports.

    Results are cached in-memory with a TTL to avoid hammering the worker.

    Args:
        force(bool): If `True`, bypass the cache and refresh from the worker.

    Returns:
        list[str]: The supported language codes (e.g. `["java"]`).

    Raises:
        HTTPException: 502 if the worker is unreachable or returns an
            unexpected response.
    """
    now = time.time()

    cached: list[str] | None = _languages_cache["languages"]
    if not force and cached is not None and now < _languages_cache["expires_at"]:
        return cached

    async with _languages_lock:
        # Double-check after acquiring the lock
        cached = _languages_cache["languages"]
        if not force and cached is not None and now < _languages_cache["expires_at"]:
            return cached

        try:
            async with httpx.AsyncClient(
                base_url=pipeline_settings.DURABLE_FUNCTIONS_BASE_URL
            ) as client:
                response = await client.get("/api/languages", timeout=5)

            response.raise_for_status()
            languages: list[str] = response.json()["languages"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            logger.exception("Languages: Failed to fetch supported languages.")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to fetch supported languages.",
            ) from exc

        _languages_cache["languages"] = languages
        _languages_cache["expires_at"] = now + languages_settings.LANGUAGES_CACHE_TTL_S

        return languages


async def validate_languages(languages: list[str]) -> None:
    """Validate a requested language filter against the worker's supported set.

    Checks the provided list of languages against the cached supported languages
    and raises an HTTPException if any are unsupported. If the cache is stale, it
    will refresh it once before raising an error.

    Args:
        languages(list[str]): The languages to analyze.

    Raises:
        HTTPException: 422 if any language is not supported by the worker.
    """
    if not languages:
        return

    supported = set(await get_supported_languages())
    invalid = [language for language in languages if language not in supported]
    if invalid:
        # Cache may be stale (missing a newly-added language); refresh once.
        supported = set(await get_supported_languages(force=True))
        invalid = [language for language in languages if language not in supported]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported language(s): {invalid}",
        )
