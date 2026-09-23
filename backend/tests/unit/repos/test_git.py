"""Unit tests for the git remote resolver.

These tests install a mocked ``httpx.AsyncClient`` so the git smart-HTTP
``info/refs`` advertisement is served from a crafted pkt-line payload (no
network).
"""

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.repos.git import repo_name_from_url, resolve_git_ref
from tests.unit.mocks.httpx_client import mock_raw_httpx_client

_ADVERTISEMENT_MEDIA_TYPE = "application/x-git-upload-pack-advertisement"
_CAPS = b"multi_ack symref=HEAD:refs/heads/main agent=git/2"
_PROXY = "http://proxy.corp:8080"
_SHA_DEV = "a" * 40
_SHA_LOOSE = "b" * 40
_SHA_MAIN = "c" * 40
_URL = "https://github.com/octo/repo"


def _pkt(payload: bytes) -> bytes:
    """Frame a payload as a git pkt-line (4-hex length prefix, inclusive)."""
    return f"{len(payload) + 4:04x}".encode() + payload


# A `git-upload-pack` advertisement with main (the default branch) and dev
_ADVERTISEMENT = (
    _pkt(b"# service=git-upload-pack\n")
    + b"0000"
    + _pkt(_SHA_MAIN.encode() + b" HEAD\x00" + _CAPS + b"\n")
    + _pkt(_SHA_MAIN.encode() + b" refs/heads/main\n")
    + _pkt(_SHA_DEV.encode() + b" refs/heads/dev\n")
    + b"0000"
)


class TestRepoNameFromUrl:
    """Derive a blob-safe repository name from a remote URL."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            pytest.param(_URL, "repo", id="plain"),
            pytest.param(
                "https://gitlab.corp/group/sub/deep-repo",
                "deep-repo",
                id="nested-group",
            ),
            pytest.param("https://git.example.com", "repo", id="no-path-falls-back"),
            pytest.param(
                "https://git.example.com/", "repo", id="empty-path-falls-back"
            ),
            pytest.param(
                "https://git.example.com/octo/we!rd na me",
                "we-rd-na-me",
                id="sanitized",
            ),
            pytest.param(
                "https://git.example.com/octo/a.b_c-d",
                "a.b_c-d",
                id="safe-punctuation-kept",
            ),
            pytest.param(
                "https://gitlab.serv.cdc.fr/cds.maltac-e/ci-ok.git",
                "ci-ok",
                id="git-suffix-dropped",
            ),
            pytest.param(f"{_URL}.git/", "repo", id="git-suffix-and-trailing-slash"),
            pytest.param(
                "https://git.example.com/octo/.git", "repo", id="bare-git-suffix"
            ),
        ],
    )
    def test_repo_name_from_url(self, url: str, expected: str) -> None:
        """The name is the URL's last path segment, reduced to a blob-safe token."""
        assert repo_name_from_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            pytest.param("https://git.example.com/octo/a/b", id="extra-segment"),
            pytest.param("https://git.example.com/octo/..", id="parent-traversal"),
            pytest.param("https://git.example.com/octo/a%2Fb", id="encoded-separator"),
            pytest.param(
                "https://git.example.com/octo/...git", id="dots-then-git-suffix"
            ),
        ],
    )
    def test_repo_name_from_url_never_yields_a_separator(self, url: str) -> None:
        """The name can never add depth: the worker's path arithmetic depends on it."""
        name = repo_name_from_url(url)

        assert "/" not in name
        assert name not in ("", ".", "..")


class TestResolveGitRef:
    """Resolve a branch or commit against a remote's ref advertisement."""

    @pytest.fixture
    def git_remote(
        self,
        mock_httpx: Callable[[AsyncMock], AsyncMock],
    ) -> Callable[..., AsyncMock]:
        """Serve a canned `info/refs` response from the git remote."""

        def serve(
            *,
            status_code: int = 200,
            content: bytes = _ADVERTISEMENT,
            content_type: str = _ADVERTISEMENT_MEDIA_TYPE,
            request_error: Exception | None = None,
        ) -> AsyncMock:
            return mock_httpx(
                mock_raw_httpx_client(
                    status_code=status_code,
                    content=content,
                    content_type=content_type,
                    request_error=request_error,
                )
            )

        return serve

    @pytest.mark.parametrize(
        ("branch", "commit", "expected_commit", "expected_ref"),
        [
            pytest.param(None, None, _SHA_MAIN, "main", id="default-branch"),
            pytest.param("dev", None, _SHA_DEV, "dev", id="explicit-branch"),
            pytest.param(
                None, _SHA_DEV.upper(), _SHA_DEV, "dev", id="commit-reverse-maps-ref"
            ),
            pytest.param(
                None, _SHA_LOOSE, _SHA_LOOSE, None, id="commit-no-matching-tip"
            ),
        ],
    )
    async def test_resolve_ref(
        self,
        git_remote: Callable[..., AsyncMock],
        branch: str | None,
        commit: str | None,
        expected_commit: str,
        expected_ref: str | None,
    ) -> None:
        """Branch/commit inputs resolve to the expected commit and fetch-hint ref."""
        git_remote()

        resolved = await resolve_git_ref(_URL, branch, commit)

        assert resolved.commit == expected_commit
        assert resolved.ref == expected_ref

    @pytest.mark.parametrize(
        ("token", "expects_auth"),
        [
            pytest.param("glpat-secret", True, id="with-token"),
            pytest.param(None, False, id="without-token"),
        ],
    )
    async def test_token_sent_as_basic_auth(
        self,
        git_remote: Callable[..., AsyncMock],
        token: str | None,
        expects_auth: bool,
    ) -> None:
        """A token is presented as HTTP Basic auth; without one, no auth is sent."""
        client = git_remote()

        await resolve_git_ref(_URL, None, None, token)

        auth = client.get.call_args.kwargs["auth"]
        assert isinstance(auth, httpx.BasicAuth) is expects_auth

    @pytest.mark.parametrize(
        ("url", "branch", "commit"),
        [
            pytest.param(_URL, "nope", None, id="unknown-branch"),
            pytest.param(_URL, None, "not-a-sha!", id="non-hexadecimal-commit"),
            pytest.param("git@github.com:octo/repo.git", None, None, id="scp-url"),
            pytest.param("ssh://git@host/x", None, None, id="ssh-url"),
        ],
    )
    async def test_invalid_request_raises_422(
        self,
        git_remote: Callable[..., AsyncMock],
        url: str,
        branch: str | None,
        commit: str | None,
    ) -> None:
        """Malformed or unsatisfiable inputs raise HTTP 422.

        The remote is healthy in every case, so the rejection comes from the input
        itself — the non-http(s) URLs are turned away before any request is made.
        """
        git_remote()

        with pytest.raises(HTTPException) as exc_info:
            await resolve_git_ref(url, branch, commit)

        assert exc_info.value.status_code == 422

    @pytest.mark.parametrize(
        ("status_code", "content", "content_type"),
        [
            pytest.param(401, b"", _ADVERTISEMENT_MEDIA_TYPE, id="unauthorized"),
            pytest.param(403, b"", _ADVERTISEMENT_MEDIA_TYPE, id="forbidden"),
            pytest.param(404, b"", _ADVERTISEMENT_MEDIA_TYPE, id="not-found"),
            pytest.param(500, b"", _ADVERTISEMENT_MEDIA_TYPE, id="server-error"),
            pytest.param(
                200,
                b"<html>Sign in</html>",
                "text/html; charset=utf-8",
                id="sso-interstitial",
            ),
        ],
    )
    async def test_unusable_remote_raises_422(
        self,
        git_remote: Callable[..., AsyncMock],
        status_code: int,
        content: bytes,
        content_type: str,
    ) -> None:
        """A remote that answers without a git advertisement surfaces as HTTP 422.

        Every case passes an explicit commit and a token: without the media-type
        check, the commit would be accepted off an empty ref map — handing an
        unusable token to the worker instead of failing here.
        """
        git_remote(status_code=status_code, content=content, content_type=content_type)

        with pytest.raises(HTTPException) as exc_info:
            await resolve_git_ref(_URL, None, _SHA_MAIN, "glpat-secret")

        assert exc_info.value.status_code == 422

    async def test_network_error_raises_502(
        self, git_remote: Callable[..., AsyncMock]
    ) -> None:
        """A transport error reaching the remote surfaces as HTTP 502."""
        git_remote(request_error=httpx.ConnectError("Connection refused"))

        with pytest.raises(HTTPException) as exc_info:
            await resolve_git_ref(_URL, None, None)

        assert exc_info.value.status_code == 502

    @pytest.mark.parametrize(
        "raw_url",
        [
            pytest.param(f"{_URL}.git", id="git-suffix"),
            pytest.param(f"{_URL}/", id="trailing-slash"),
            pytest.param(f"{_URL}.git/", id="git-suffix-and-trailing-slash"),
            pytest.param(f"  {_URL}  ", id="surrounding-whitespace"),
        ],
    )
    async def test_normalize_url(
        self, git_remote: Callable[..., AsyncMock], raw_url: str
    ) -> None:
        """Trailing slashes, a `.git` suffix, and whitespace are stripped."""
        git_remote()

        resolved = await resolve_git_ref(raw_url, None, None)

        assert resolved.url == _URL


class TestProxyFallback:
    """Retry a remote through `HTTP_PROXY_URL` when it is not reachable directly."""

    @staticmethod
    def _response(
        *,
        status_code: int = 200,
        content: bytes = _ADVERTISEMENT,
        content_type: str = _ADVERTISEMENT_MEDIA_TYPE,
    ) -> MagicMock:
        """Build an `info/refs` response double for a single attempt."""
        response = MagicMock()
        response.status_code = status_code
        response.content = content
        response.headers = {"content-type": content_type}
        return response

    @pytest.fixture
    def git_routes(self, monkeypatch: pytest.MonkeyPatch) -> Callable[..., MagicMock]:
        """Answer each attempt in turn, capturing the proxy every client was given."""

        def serve(*attempts: object) -> MagicMock:
            client = mock_raw_httpx_client()
            client.get.side_effect = attempts
            constructor = MagicMock(return_value=client)
            monkeypatch.setattr(httpx, "AsyncClient", constructor)
            return constructor

        return serve

    async def test_direct_success_skips_the_proxy(
        self,
        monkeypatch: pytest.MonkeyPatch,
        git_routes: Callable[..., MagicMock],
    ) -> None:
        """A remote reachable directly is never retried through the proxy."""
        monkeypatch.setattr(settings, "HTTP_PROXY_URL", _PROXY)
        constructor = git_routes(self._response())

        resolved = await resolve_git_ref(_URL)

        assert resolved.commit == _SHA_MAIN
        assert constructor.call_count == 1
        assert constructor.call_args.kwargs["proxy"] is None

    @pytest.mark.parametrize(
        "direct",
        [
            pytest.param(
                httpx.ConnectError("Connection refused"), id="transport-error"
            ),
            pytest.param(
                _response(
                    content=b"<html>Blocked</html>",
                    content_type="text/html; charset=utf-8",
                ),
                id="block-page",
            ),
        ],
    )
    async def test_proxy_serves_what_direct_cannot(
        self,
        monkeypatch: pytest.MonkeyPatch,
        git_routes: Callable[..., MagicMock],
        direct: object,
    ) -> None:
        """A remote direct cannot serve still resolves through the proxy."""
        monkeypatch.setattr(settings, "HTTP_PROXY_URL", _PROXY)
        constructor = git_routes(direct, self._response())

        resolved = await resolve_git_ref(_URL)

        assert resolved.commit == _SHA_MAIN
        assert constructor.call_count == 2
        assert constructor.call_args.kwargs["proxy"] == _PROXY

    async def test_unconfigured_proxy_is_not_tried(
        self,
        monkeypatch: pytest.MonkeyPatch,
        git_routes: Callable[..., MagicMock],
    ) -> None:
        """Without `HTTP_PROXY_URL`, an unreachable remote fails on the first leg."""
        monkeypatch.setattr(settings, "HTTP_PROXY_URL", None)
        constructor = git_routes(httpx.ConnectError("Connection refused"))

        with pytest.raises(HTTPException) as exc_info:
            await resolve_git_ref(_URL)

        assert exc_info.value.status_code == 502
        assert constructor.call_count == 1

    @pytest.mark.parametrize(
        ("direct", "through_proxy", "expected_status"),
        [
            pytest.param(
                httpx.ConnectError("Connection refused"),
                _response(status_code=404),
                422,
                id="proxy-answers-not-found",
            ),
            pytest.param(
                _response(content=b"<html>Blocked</html>", content_type="text/html"),
                httpx.ConnectError("Connection refused"),
                502,
                id="proxy-unreachable",
            ),
        ],
    )
    async def test_failure_describes_the_last_route(
        self,
        monkeypatch: pytest.MonkeyPatch,
        git_routes: Callable[..., MagicMock],
        direct: object,
        through_proxy: object,
        expected_status: int,
    ) -> None:
        """When both routes fail, the proxy attempt decides 502 versus 422."""
        monkeypatch.setattr(settings, "HTTP_PROXY_URL", _PROXY)
        constructor = git_routes(direct, through_proxy)

        with pytest.raises(HTTPException) as exc_info:
            await resolve_git_ref(_URL)

        assert exc_info.value.status_code == expected_status
        assert constructor.call_count == 2
