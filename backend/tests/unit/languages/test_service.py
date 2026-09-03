"""Unit tests for the languages service.

This module tests `get_supported_languages`: fetching and parsing the worker
response, in-memory caching with a forced-refresh bypass, and error handling.
"""

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.languages import service
from app.languages.service import get_supported_languages


@pytest.fixture(autouse=True)
def _reset_cache() -> Generator[None, None, None]:
    """Reset the module-level languages cache around each test."""
    service._languages_cache = {"languages": None, "expires_at": 0}
    yield
    service._languages_cache = {"languages": None, "expires_at": 0}


def _mock_client(payload: dict[str, list[str]]) -> AsyncMock:
    """Build a mocked `httpx.AsyncClient` whose GET returns payload."""
    mock_response = MagicMock()
    mock_response.json.return_value = payload
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    return mock_client


def _failing_client(
    *,
    get_error: Exception | None = None,
    status_error: Exception | None = None,
    json_error: Exception | None = None,
    json_value: dict[str, Any] | None = None,
) -> AsyncMock:
    """Build a mocked `httpx.AsyncClient` that fails at a chosen step.

    Exactly one failure mode should be supplied so the test exercises a single
    branch of the service's `except (httpx.HTTPError, ValueError, KeyError)`.
    """
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock(side_effect=status_error)
    if json_error is not None:
        mock_response.json.side_effect = json_error
    else:
        mock_response.json.return_value = json_value

    mock_client = AsyncMock()
    if get_error is not None:
        mock_client.get.side_effect = get_error
    else:
        mock_client.get.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    return mock_client


async def test_fetches_and_parses_languages() -> None:
    """Parses the `languages` array and caches it (second call hits the cache)."""
    mock_client = _mock_client({"languages": ["java"]})

    with patch.object(httpx, "AsyncClient", return_value=mock_client):
        first = await get_supported_languages()
        cached = await get_supported_languages()

    assert first == ["java"]
    assert cached == ["java"]
    mock_client.get.assert_awaited_once()


async def test_force_refresh_bypasses_cache() -> None:
    """`force=True` re-fetches from the worker even when the cache is warm."""
    mock_client = _mock_client({"languages": ["java"]})

    with patch.object(httpx, "AsyncClient", return_value=mock_client):
        await get_supported_languages()
        await get_supported_languages(force=True)

    assert mock_client.get.await_count == 2


@pytest.mark.parametrize(
    "client",
    [
        pytest.param(
            _failing_client(get_error=httpx.ConnectError("Connection refused")),
            id="connect-error",
        ),
        pytest.param(
            _failing_client(status_error=httpx.HTTPError("500 Server Error")),
            id="bad-status",
        ),
        pytest.param(
            _failing_client(json_error=ValueError("Malformed JSON")),
            id="invalid-json",
        ),
        pytest.param(
            _failing_client(json_value={}),
            id="missing-languages-key",
        ),
    ],
)
async def test_worker_error_raises_502(client: AsyncMock) -> None:
    """Any worker/parsing failure surfaces as HTTP 502."""
    with patch.object(httpx, "AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc_info:
            await get_supported_languages()

    assert exc_info.value.status_code == 502
