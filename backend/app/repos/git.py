"""Git remote resolution.

This module resolves a git URL (optionally narrowed by a branch and/or a
specific commit) to a concrete, accessible commit, using the git smart-HTTP
protocol (`GET <url>/info/refs?service=git-upload-pack`). No git binary is
required — the advertised refs are parsed from the pkt-line response. The
backend never clones. It only verifies accessibility and pins the commit
before handing the URL + commit to the analysis worker.

A remote that is not reachable directly is retried through `HTTP_PROXY_URL`
when one is configured, so directly reachable and proxy-only remotes both work
without anyone declaring which is which.
"""

import re
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, status
from loguru import logger
from pydantic import BaseModel

from app.core.config import settings

# HTTP timeout for the info/refs call
GIT_UPLOAD_PACK_TIMEOUT_S = 10

_ADVERTISEMENT_MEDIA_TYPE = "application/x-git-upload-pack-advertisement"
_COMMIT_RE = re.compile(r"[0-9a-fA-F]{7,64}")
_HEADS_PREFIX = "refs/heads/"
_SYMREF_HEAD_PREFIX = b"symref=HEAD:"
_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


class ResolvedGitRef(BaseModel):
    """A git request resolved to an accessible commit.

    Attributes:
        url(str): The normalized (canonical) repository URL.
        commit(str): The resolved commit SHA — the sole ref identity that
            persists (stored as `repo_hash`).
        ref(str | None): The branch the commit was resolved from, forwarded to
            the worker as a transient fetch hint. Never stored or displayed.
    """

    url: str
    commit: str
    ref: str | None = None


async def _get_advertisement(
    url: str,
    auth: httpx.BasicAuth | None,
    proxy: str | None,
) -> httpx.Response | None:
    """Fetch a `git-upload-pack` advertisement over one route.

    Args:
        url(str): The normalized repository URL.
        auth(httpx.BasicAuth | None): Credentials for a private remote.
        proxy(str | None): The proxy to route through, or `None` to go direct.

    Returns:
        httpx.Response | None: The advertisement, or `None` when the route
            answered with something that is not one — 401/403 (auth), 404
            (missing), and a 200 that is not a smart-HTTP advertisement (an
            SSO/proxy sign-in page) all mean "not accessible over this route".
    """
    async with httpx.AsyncClient(follow_redirects=True, proxy=proxy) as client:
        response = await client.get(
            f"{url}/info/refs",
            params={"service": "git-upload-pack"},
            auth=auth,
            timeout=GIT_UPLOAD_PACK_TIMEOUT_S,
        )

    media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if response.status_code != 200 or media_type != _ADVERTISEMENT_MEDIA_TYPE:
        return None

    return response


def _iter_pkt_lines(data: bytes) -> list[bytes]:
    """Split a git pkt-line stream into its payload lines (flush packets dropped).

    Args:
        data(bytes): The raw `info/refs` response body.

    Returns:
        list[bytes]: The payload of each non-flush pkt-line.
    """
    lines: list[bytes] = []
    index = 0
    total = len(data)
    while index + 4 <= total:
        try:
            length = int(data[index : index + 4], 16)
        except ValueError:
            break
        if length == 0:  # flush packet
            index += 4
            continue
        if length < 4 or index + length > total:
            break
        lines.append(data[index + 4 : index + length])
        index += length
    return lines


def _normalize_repo_url(repo_url: str) -> str:
    """Canonicalize a git URL for accessibility checks and deduplication.

    Args:
        repo_url(str): The raw repository URL.

    Returns:
        str: The URL with any trailing `/` and `.git` suffix removed.

    Raises:
        HTTPException: 422 if the URL is not an http(s) URL.
    """
    url = repo_url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Repository URL must be an http(s) URL.",
        )
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[: -len(".git")]
    return url


def _parse_advertised_refs(payload: bytes) -> tuple[dict[str, str], str | None]:
    """Parse a `git-upload-pack` advertisement into refs and the default branch.

    Args:
        payload(bytes): The raw `info/refs` response body.

    Returns:
        tuple[dict[str, str], str | None]: A mapping of ref name to commit SHA,
            and the default branch ref name from the `HEAD` symref capability
            (`None` if not advertised).
    """
    refs: dict[str, str] = {}
    head_symref: str | None = None

    for line in _iter_pkt_lines(payload):
        if line.startswith(b"# service="):
            continue
        # Capabilities (only on the first ref line) follow a NUL byte
        caps = b""
        if b"\x00" in line:
            line, caps = line.split(b"\x00", 1)
        line = line.rstrip(b"\n")
        if not line:
            continue
        parts = line.split(b" ", 1)
        if len(parts) != 2:
            continue
        sha, refname = parts
        refs[refname.decode()] = sha.decode()

        if caps and head_symref is None:
            for cap in caps.split():
                if cap.startswith(_SYMREF_HEAD_PREFIX):
                    head_symref = cap[len(_SYMREF_HEAD_PREFIX) :].decode()
                    break

    return refs, head_symref


def repo_name_from_url(url: str) -> str:
    """Derive a blob-safe repository name from a git URL.

    Used as the leaf of a git source path so it mirrors the shape a zip upload
    produces. Derived from the URL rather than from a caller's display name, so
    two users adding the same repository still address one shared clone.

    Args:
        url(str): The repository URL. Normalized or raw: a trailing `.git` is
            tolerated and dropped, so the name never depends on which form of
            the URL a caller happens to hold.

    Returns:
        str: The URL's last path segment without its `.git` extension, reduced
            to `A-Za-z0-9._-` so it can never introduce a path separator, or
            `repo` when the URL carries no path or only a dotted one.
    """
    segment = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    name = _UNSAFE_NAME_RE.sub("-", segment)

    # `.` and `..` are safe in a flat blob name but not worth carrying into one
    return name if name.strip(".") else "repo"


async def resolve_git_ref(
    repo_url: str,
    branch: str | None = None,
    commit: str | None = None,
    token: str | None = None,
) -> ResolvedGitRef:
    """Verify a git remote is accessible and resolve it to a concrete commit.

    The remote is tried directly first. When that yields no advertisement and
    `HTTP_PROXY_URL` is configured, it is tried again through the proxy, so a
    remote reachable only one of the two ways resolves either way.

    Args:
        repo_url(str): The repository URL.
        branch(str | None): Optional branch to resolve the commit from.
        commit(str | None): Optional explicit commit SHA (wins over branch).
        token(str | None): Optional credential for private remotes (a personal
            access / deploy token, sent via HTTP Basic auth as `oauth2:<token>`
            — the portable convention across GitLab/GitHub/Bitbucket; git-HTTP
            does not accept Bearer).

    Returns:
        ResolvedGitRef: The normalized URL, resolved commit, and fetch-hint ref.

    Raises:
        HTTPException: 422 if the URL is malformed, the remote is inaccessible
            or does not answer with a git advertisement, the branch does not
            exist, or the commit is malformed. 502 if the remote is unreachable.
    """
    url = _normalize_repo_url(repo_url)

    if commit is not None:
        if not _COMMIT_RE.fullmatch(commit):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Commit must be a hexadecimal SHA.",
            )
        commit = commit.lower()

    auth = httpx.BasicAuth("oauth2", token) if token else None

    routes: tuple[str | None, ...] = (None,)
    if settings.HTTP_PROXY_URL:
        routes = (None, settings.HTTP_PROXY_URL)

    response = None
    unreachable: Exception | None = None
    for proxy in routes:
        unreachable = None
        try:
            response = await _get_advertisement(url, auth, proxy)
        except httpx.HTTPError as exc:
            logger.exception(f"Repos: Failed to reach git remote '{url}'.")
            unreachable = exc
            continue
        if response is not None:
            break

    if unreachable is not None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach the git remote.",
        ) from unreachable
    if response is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Repository does not exist or is not accessible.",
        )

    refs, head_symref = _parse_advertised_refs(response.content)

    # Explicit commit: accept it (accessibility confirmed) and reverse-map a
    # branch tip to a fetch hint when one matches
    if commit is not None:
        ref = next(
            (
                name[len(_HEADS_PREFIX) :]
                for name, sha in refs.items()
                if sha == commit and name.startswith(_HEADS_PREFIX)
            ),
            None,
        )
        return ResolvedGitRef(url=url, commit=commit, ref=ref)

    # Explicit branch: resolve to its tip
    if branch:
        sha = refs.get(f"{_HEADS_PREFIX}{branch}")
        if sha is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Branch '{branch}' not found in the repository.",
            )
        return ResolvedGitRef(url=url, commit=sha, ref=branch)

    # Neither: use the remote's default branch (HEAD symref), falling back to a
    # heads ref sharing HEAD's SHA
    default_ref = head_symref
    head_sha = refs.get("HEAD")
    if default_ref is None and head_sha is not None:
        default_ref = next(
            (
                name
                for name, sha in refs.items()
                if name.startswith(_HEADS_PREFIX) and sha == head_sha
            ),
            None,
        )

    default_sha = refs.get(default_ref) if default_ref is not None else None
    default_sha = default_sha or head_sha
    if default_ref is None or default_sha is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Could not determine the repository's default branch.",
        )

    ref = (
        default_ref[len(_HEADS_PREFIX) :]
        if default_ref.startswith(_HEADS_PREFIX)
        else default_ref
    )
    return ResolvedGitRef(url=url, commit=default_sha, ref=ref)
