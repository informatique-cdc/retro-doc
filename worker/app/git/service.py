"""Git service.

This module defines the service layer for fetching a git repository at a
pinned commit. The backend resolves and pins the commit before dispatching, so
this worker only has to materialize that exact tree.
"""

import asyncio
import base64
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from loguru import logger

from app.core.config import settings

CLONE_TIMEOUT_S = 900
"""Per git invocation. The host.json functionTimeout is 01:00:00 and a clone is
only the first step of the activity, so stay well under it.
"""


class GitCloneError(Exception):
    """Raised when a repository cannot be materialized at the wanted commit."""


def _build_env(token: str | None, proxy: str | None) -> dict[str, str]:
    """Build the environment for a git invocation.

    The token is passed as an HTTP header through GIT_CONFIG_* rather than
    embedded in the remote URL: git echoes that URL in its error output, so a
    credential there would end up in the logs.

    Args:
        token(str | None): The access token for a private repository, if any.
        proxy(str | None): The proxy to route through, or None to go direct.

    Returns:
        dict[str, str]: The environment to run git with.
    """
    # Inherit rather than replace, so PATH and any proxy settings come along
    env = os.environ.copy()
    # Fail fast instead of blocking on an interactive credential prompt, which
    # would hang the activity until the function timeout
    env["GIT_TERMINAL_PROMPT"] = "0"

    entries: list[tuple[str, str]] = []

    if token is not None:
        # `oauth2:<token>` Basic auth is the portable convention across
        # GitLab/GitHub/Bitbucket; git-HTTP does not accept Bearer. The header
        # is deliberately unscoped (no `http.<url>.` prefix): one invocation
        # only ever talks to one remote, so scoping would only drag in git's
        # URL prefix matching and host redirect behavior.
        basic = base64.b64encode(f"oauth2:{token}".encode()).decode()
        entries.append(("http.extraheader", f"Authorization: Basic {basic}"))

    if proxy is not None:
        # git's own knob rather than HTTPS_PROXY: it covers http and https
        # remotes with one entry and outranks whatever the environment already
        # set, so the proxied route is proxied for sure.
        entries.append(("http.proxy", proxy))

    env["GIT_CONFIG_COUNT"] = str(len(entries))
    for index, (key, value) in enumerate(entries):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value

    return env


async def _clone_at_commit(
    dest: Path,
    repo_url: str,
    commit: str | None,
    ref: str | None,
    token: str | None,
    proxy: str | None,
) -> str:
    """Materialize a repository's working tree over one route.

    Args:
        dest(Path): The directory to check the working tree out into.
        repo_url(str): The remote repository URL.
        commit(str | None): The commit to pin to. None checks out the tip.
        ref(str | None): The branch the commit was resolved from, if known.
        token(str | None): The access token for a private repository, if any.
        proxy(str | None): The proxy to route through, or None to go direct.

    Returns:
        str: The resolved commit SHA of the checked out tree.

    Raises:
        GitCloneError: If the repository cannot be fetched at that commit.
    """
    env = _build_env(token, proxy)

    if commit is None:
        # The backend always pins in practice, but the key can carry a null.
        logger.warning(f"Git: No commit pinned for '{repo_url}', using the tip")
        await _init_remote(dest, repo_url, env)
        await _run_git(
            ["fetch", "--depth", "1", "--no-tags", "origin", ref or "HEAD"], dest, env
        )
        await _run_git(["checkout", "-q", "--detach", "FETCH_HEAD"], dest, env)
        return await _run_git(["rev-parse", "HEAD"], dest, env)

    # Fetching a bare SHA needs uploadpack.allowReachableSHA1InWant, which
    # GitHub, GitLab and Bitbucket all enable. This is the only path taken
    # in the normal case.
    await _init_remote(dest, repo_url, env)
    try:
        await _run_git(
            ["fetch", "--depth", "1", "--no-tags", "origin", commit], dest, env
        )
    except GitCloneError as err:
        if ref is None:
            raise
        # The host refused the SHA. Fall back to the branch it was resolved
        # from, which only helps while the commit is still that branch's tip.
        logger.warning(
            f"Git: Fetching '{commit}' directly failed ({err}), retrying via '{ref}'"
        )
        await _init_remote(dest, repo_url, env)
        await _run_git(["fetch", "--depth", "1", "--no-tags", "origin", ref], dest, env)
        fetched = await _run_git(["rev-parse", "FETCH_HEAD"], dest, env)
        if fetched != commit:
            raise GitCloneError(
                f"'{ref}' is now at '{fetched}', not the pinned '{commit}'"
            ) from err

    await _run_git(["checkout", "-q", "--detach", commit], dest, env)
    logger.debug(f"Git: Checked out '{repo_url}' at '{commit}'")

    return commit


async def _init_remote(dest: Path, repo_url: str, env: dict[str, str]) -> None:
    """Create an empty repository in `dest` pointing at `repo_url`.

    Args:
        dest(Path): The directory to initialize. Recreated if it already exists.
        repo_url(str): The remote repository URL.
        env(dict[str, str]): The environment to run git with.
    """
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    await _run_git(["init", "-q"], dest, env)
    await _run_git(["remote", "add", "origin", repo_url], dest, env)


async def _run_git(args: Sequence[str], cwd: Path, env: dict[str, str]) -> str:
    """Run one git invocation and return its stdout.

    Args:
        args(Sequence[str]): The git arguments, without the leading `git`.
        cwd(Path): The working directory to run git in.
        env(dict[str, str]): The environment to run git with.

    Returns:
        str: The stripped standard output of the invocation.

    Raises:
        GitCloneError: If git exits non-zero or exceeds CLONE_TIMEOUT_S.
    """
    # Safe by construction: a fixed argv list, no shell, and no credential in it
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=CLONE_TIMEOUT_S
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        raise GitCloneError(
            f"git {' '.join(args)} timed out after {CLONE_TIMEOUT_S}s"
        ) from None

    if process.returncode != 0:
        raise GitCloneError(
            f"git {' '.join(args)} failed ({process.returncode}): "
            f"{stderr.decode(errors='replace').strip()}"
        )

    return stdout.decode(errors="replace").strip()


async def clone_at_commit(
    dest: Path,
    repo_url: str,
    commit: str | None,
    ref: str | None,
    token: str | None,
) -> str:
    """Materialize a repository's working tree at a pinned commit.

    The remote is fetched directly first and, when that fails and
    `HTTP_PROXY_URL` is configured, fetched again through the proxy, so a
    remote reachable only one of the two ways materializes either way. The
    whole materialization is retried rather than just the failed fetch, which
    `_init_remote` makes safe by recreating `dest` on entry.

    Args:
        dest(Path): The directory to check the working tree out into.
        repo_url(str): The remote repository URL.
        commit(str | None): The commit to pin to. None checks out the tip.
        ref(str | None): The branch the commit was resolved from, if known.
        token(str | None): The access token for a private repository, if any.

    Returns:
        str: The resolved commit SHA of the checked out tree.

    Raises:
        GitCloneError: If the repository cannot be fetched at that commit.
    """
    try:
        return await _clone_at_commit(dest, repo_url, commit, ref, token, None)
    except GitCloneError as err:
        if settings.HTTP_PROXY_URL is None:
            raise
        # Nobody declares which remotes are direct-only and which are
        # proxy-only, so try both rather than making the caller pick.
        logger.warning(
            f"Git: Fetching '{repo_url}' directly failed ({err}), "
            "retrying through the proxy"
        )
        return await _clone_at_commit(
            dest, repo_url, commit, ref, token, settings.HTTP_PROXY_URL
        )
