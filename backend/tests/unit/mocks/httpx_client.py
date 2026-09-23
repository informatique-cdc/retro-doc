"""Shared `httpx.AsyncClient` mock builders for outbound calls.

Several modules reach out via `httpx.AsyncClient()`: the auth, languages and
pipeline tests parse a JSON body (over GET or POST, via `method=`), while the
git resolver reads the raw response, and the pipeline worker signals rejected
languages through a real status code. These helpers build the matching test
doubles so each test can stand in for `httpx.AsyncClient` without
re-implementing the mock. They are plain callables (not fixtures): import one,
call it, and hand the result to the `mock_httpx` fixture in
`tests/unit/conftest.py`, which installs it for the test and returns it.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx


def mock_httpx_client(payload: dict[str, Any], *, method: str = "get") -> AsyncMock:
    """Build a mocked `httpx.AsyncClient` whose `method` call returns payload."""
    mock_response = MagicMock()
    mock_response.json.return_value = payload
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    getattr(mock_client, method).return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    return mock_client


def mock_failing_httpx_client(
    *,
    method: str = "get",
    request_error: Exception | None = None,
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
    if request_error is not None:
        getattr(mock_client, method).side_effect = request_error
    else:
        getattr(mock_client, method).return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    return mock_client


def mock_status_httpx_client(
    status_code: int, *, method: str = "get", url: str = "http://test"
) -> AsyncMock:
    """Build a mocked `httpx.AsyncClient` returning a real `httpx.Response`.

    For callers that let `raise_for_status()` produce a genuine
    `HTTPStatusError` and then read `.response.status_code` off it.
    """
    request = httpx.Request(method.upper(), url)
    mock_response = httpx.Response(status_code=status_code, request=request)

    mock_client = AsyncMock()
    getattr(mock_client, method).return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    return mock_client


def mock_raw_httpx_client(
    *,
    status_code: int = 200,
    content: bytes = b"",
    content_type: str = "",
    request_error: Exception | None = None,
) -> AsyncMock:
    """Build a mocked `httpx.AsyncClient` whose GET returns a raw response.

    For callers that read `status_code` / `content` / `headers` directly instead
    of `.json()` — e.g. the git smart-HTTP `info/refs` advertisement. Supplying
    `request_error` makes the request itself fail instead.
    """
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.content = content
    mock_response.headers = {"content-type": content_type}

    mock_client = AsyncMock()
    if request_error is not None:
        mock_client.get.side_effect = request_error
    else:
        mock_client.get.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    return mock_client
