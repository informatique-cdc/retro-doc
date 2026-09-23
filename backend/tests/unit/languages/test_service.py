"""Unit tests for the languages service.

This module tests the languages service related to fetching and validating
supported programming languages.
"""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from app.languages import service
from app.languages.service import get_supported_languages, validate_languages
from tests.unit.mocks.httpx_client import mock_failing_httpx_client, mock_httpx_client


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty the module-level languages cache for the duration of each test."""
    monkeypatch.setattr(
        service, "_languages_cache", {"languages": None, "expires_at": 0}
    )


class TestGetSupportedLanguages:
    """Fetch the worker's supported languages behind a TTL cache."""

    @pytest.mark.parametrize(
        ("force", "expected_gets"),
        [
            pytest.param(False, 1, id="second-call-hits-cache"),
            pytest.param(True, 2, id="force-bypasses-cache"),
        ],
    )
    async def test_fetches_parses_and_caches_languages(
        self,
        force: bool,
        expected_gets: int,
        mock_httpx: Callable[[AsyncMock], AsyncMock],
    ) -> None:
        """Parses the `languages` array, then serves it from the cache unless forced."""
        client = mock_httpx(mock_httpx_client({"languages": ["java"]}))

        first = await get_supported_languages()
        second = await get_supported_languages(force=force)

        assert first == ["java"]
        assert second == ["java"]
        assert client.get.await_count == expected_gets

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(
                {"request_error": httpx.ConnectError("Connection refused")},
                id="connect-error",
            ),
            pytest.param(
                {"status_error": httpx.HTTPError("500 Server Error")},
                id="bad-status",
            ),
            pytest.param(
                {"json_error": ValueError("Malformed JSON")}, id="invalid-json"
            ),
            pytest.param({"json_value": {}}, id="missing-languages-key"),
        ],
    )
    async def test_worker_error_raises_502(
        self,
        failure: dict[str, Any],
        mock_httpx: Callable[[AsyncMock], AsyncMock],
    ) -> None:
        """Any worker/parsing failure surfaces as HTTP 502."""
        mock_httpx(mock_failing_httpx_client(**failure))

        with pytest.raises(HTTPException) as exc_info:
            await get_supported_languages()

        assert exc_info.value.status_code == 502


class TestValidateLanguages:
    """Check a requested language filter against the supported set."""

    @pytest.fixture
    def mock_supported_languages(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        """Patch `get_supported_languages` as `validate_languages` looks it up."""
        mock = AsyncMock()
        monkeypatch.setattr(service, "get_supported_languages", mock)
        return mock

    async def test_empty_filter_skips_the_worker(
        self, mock_supported_languages: AsyncMock
    ) -> None:
        """An empty filter means 'all supported' and needs no worker call."""
        await validate_languages([])

        mock_supported_languages.assert_not_awaited()

    async def test_supported_filter_passes_without_refresh(
        self, mock_supported_languages: AsyncMock
    ) -> None:
        """A fully supported filter is accepted using only the cached set."""
        mock_supported_languages.return_value = ["java"]

        await validate_languages(["java"])

        mock_supported_languages.assert_awaited_once_with()

    async def test_stale_cache_is_refreshed_before_accepting(
        self, mock_supported_languages: AsyncMock
    ) -> None:
        """A miss against a stale cache force-refreshes, then accepts the language."""
        mock_supported_languages.side_effect = [["java"], ["java", "rust"]]

        await validate_languages(["rust"])

        assert mock_supported_languages.await_args_list[-1].kwargs == {"force": True}

    async def test_unsupported_language_raises_422_after_refresh(
        self, mock_supported_languages: AsyncMock
    ) -> None:
        """A language missing from the refreshed set is rejected with 422."""
        mock_supported_languages.return_value = ["java"]

        with pytest.raises(HTTPException) as exc_info:
            await validate_languages(["cobol"])

        assert exc_info.value.status_code == 422
        assert "cobol" in exc_info.value.detail
        assert mock_supported_languages.await_count == 2
