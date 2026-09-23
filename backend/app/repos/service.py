"""Repository analysis service.

This module contains the business logic for repository analysis,
separated from the HTTP/routing concerns in router.py.
"""

import hashlib
import re
from datetime import UTC, datetime
from typing import BinaryIO
from uuid import uuid4

from beanie import PydanticObjectId
from beanie.operators import GT, And, Eq, In, Inc, Or, Set
from fastapi import HTTPException, status
from packaging.version import InvalidVersion, Version
from pymongo.errors import DuplicateKeyError

from app.auth.schemas import User
from app.chat.service import delete_threads_by_repo
from app.core.blob_storage import get_container_client
from app.deep_analysis.service import delete_analyses_by_repo
from app.docs.models import FileDocumentationDocument, RepoMetaDocument
from app.graphs.models import ASTDocument, CFGDocument, DFGDocument
from app.languages.service import validate_languages
from app.pipeline.models import PipelineRunDocument, PipelineStatus
from app.pipeline.service import (
    get_analyzer_version,
    reconcile_pipeline_run,
    start_orchestration,
)
from app.repos.git import repo_name_from_url, resolve_git_ref
from app.repos.models import FileDocument, RepoDocument
from app.users.models import UserRepoDocument


async def _acquire_repo_link(
    repo_id: PydanticObjectId, user: User, name: str, color: str | None
) -> None:
    """Claim a holder's slot on a repository, then link them to it.

    `user_count` is a counter and the `UserRepoDocument` links are the real list
    of holders, so the write order decides which way they disagree after a
    crash. Claiming the slot first keeps `user_count >= live links`: a count too
    high keeps a repository nothing references (repaired by recounting) whereas too
    low drops a repository a holder still sees out of the deduplication filters
    (`_find_live_git_repo`, `_find_live_zip_repo`) and out of the partial unique
    indexes, which is unrecoverable.

    The decrement on an already-existing link is unguarded and best-effort: it
    undoes this call's own increment, and losing it costs a slot nobody holds.

    Args:
        repo_id(PydanticObjectId): The repository to join.
        user(User): The authenticated user.
        name(str): The caller's display name for the repository.
        color(str | None): The caller's optional hex color.

    Raises:
        HTTPException: 409 if the user already has this repository. 404 if the
            repository is no longer available.
    """
    inc_result = await RepoDocument.find_one(
        RepoDocument.id == repo_id,
        GT(RepoDocument.user_count, 0),
    ).update(Inc({RepoDocument.user_count: 1}))  # type: ignore[no-untyped-call]

    if inc_result.modified_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found or no longer available.",
        )

    user_repo = UserRepoDocument(
        user_id=user.uid, repo_id=repo_id, name=name, color=color
    )
    try:
        await user_repo.insert()
    except DuplicateKeyError as exc:
        await RepoDocument.find_one(RepoDocument.id == repo_id).update(
            Inc({RepoDocument.user_count: -1})  # type: ignore[no-untyped-call]
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repository already added to your account.",
        ) from exc


async def _claim_next_run(repo_id: PydanticObjectId) -> PipelineRunDocument | None:
    """Claim a repository's next analysis, unless one is already in flight.

    The claim a retry or a repair takes before dispatching, and it is the new
    run's own existence rather than a flag on the attempt it supersedes: a
    partial unique index admits one non-terminal run per repository, so
    concurrent callers race on a single insert the store arbitrates. Nothing is
    written speculatively, so nothing rolls back, and the worst a crash leaves
    is a real PENDING run the next caller can dispatch.

    Args:
        repo_id(PydanticObjectId): The RepoDocument ID.

    Returns:
        PipelineRunDocument | None: The run this call created, which it now owns
            and must dispatch, or None if another caller already holds the claim.
    """
    pipeline_run = PipelineRunDocument(repo_id=repo_id)

    try:
        await pipeline_run.insert()
    except DuplicateKeyError:
        return None

    return pipeline_run


async def _dispatch_run(
    repo: RepoDocument,
    pipeline_run: PipelineRunDocument,
    token: str | None = None,
    ref: str | None = None,
) -> None:
    """Hand a repository's source to the worker under a pending run.

    Reads the source shape off the repository rather than being told it, so
    every path that dispatches an existing repository describes it the same way:
    a zip row leaves `repo_hash` unset, making its presence what identifies a
    git source. `repo_url` is set either way, so it discriminates nothing.

    A zip is dispatched as `blob_path` plus `.zip`: `analyze_file` strips the
    suffix when storing the path, but what the worker is handed is the uploaded
    archive itself.

    Args:
        repo(RepoDocument): The repository to analyze.
        pipeline_run(PipelineRunDocument): The pending run to track it under.
        token(str | None): Optional personal or project access token, how a
            private remote is reached. Never persisted, so the caller supplies
            their own; it need not be the one that created the repository.
        ref(str | None): Optional git ref the commit was resolved from, passed
            on to the worker as a fetch hint. Only a caller that just resolved
            the remote has one, which every git caller now is: `RepoDocument`
            does not store the branch, so it cannot be recovered without asking
            the remote.

    Raises:
        HTTPException: 422 if the worker rejects the repository's languages, 502
            if the worker is unreachable.
    """
    if repo.repo_hash is not None:
        await start_orchestration(
            repo.blob_path,
            repo.languages,
            pipeline_run,
            repo_url=repo.repo_url,
            commit=repo.repo_hash,
            ref=ref,
            token=token,
        )
    else:
        await start_orchestration(
            f"{repo.blob_path}.zip",
            repo.languages,
            pipeline_run,
        )


def _git_blob_path(url: str, commit: str) -> str:
    """Build the blob storage prefix a git repository's source is stored under.

    Addressed by its determinants alone: repository and commit. The analyzer
    version is deliberately absent: source at a commit is byte-identical whatever
    analyzed it, so every version shares one clone. The shape mirrors a zip upload's
    (`.../src/{name}`) so both sit at the same depth: the worker drops a fixed
    number of leading segments to get a repository-relative path, making that depth
    part of the contract.

    Args:
        url(str): The normalized repository URL.
        commit(str): The resolved commit SHA.

    Returns:
        str: The blob storage prefix for this repository's source.
    """
    url_hash = hashlib.sha256(url.encode()).hexdigest()

    return f"git/{url_hash}/{commit}/src/{repo_name_from_url(url)}"


async def _find_latest_pipeline_run(
    repo_id: PydanticObjectId,
) -> PipelineRunDocument | None:
    """Find a repository's most recent pipeline run, if it has one.

    For the write paths, which act on the current attempt alone. A reader
    wanting the earlier attempts too takes `get_pipeline_history`, and one
    needing a run to exist takes `get_pipeline_with_attempts`, which 404s.

    None is not an error here. A repository is linked to its creator before its
    run is inserted, so a repository without one is being created at this
    instant or is a create that died in between — the callers tell those apart
    and neither is a missing repository.

    Args:
        repo_id(PydanticObjectId): The RepoDocument ID.

    Returns:
        PipelineRunDocument | None: The repository's most recent run, or None
            when it has none.
    """
    return await PipelineRunDocument.find_one(
        PipelineRunDocument.repo_id == repo_id,
        sort=[("_id", -1)],
    )


async def _find_live_git_repo(
    url: str, commit: str, analyzer_version: str
) -> RepoDocument | None:
    """Find a live (`user_count > 0`) git repo whose output is at this version.

    Matches on the version floor — `ran_analyzer_version` when the worker has
    stamped one, else `analyzer_version` — because deduplication asks "do we
    already hold this commit's output at this version?", which is a question
    about what a row produced, not about the label it was created with. A row
    whose worker redeployed mid-run carries a key that no longer describes it,
    and matching the raw field alone would miss it and pay for a duplicate
    analysis.

    Use `_find_live_git_repo_by_key` instead when recovering from a
    `DuplicateKeyError`: that asks who owns the unique key, not who holds the
    output.

    Args:
        url(str): The normalized repository URL.
        commit(str): The resolved commit SHA.
        analyzer_version(str): The worker analyzer version.

    Returns:
        RepoDocument | None: The matching repository, or None. When a divergent
            row and a cleanly-keyed row both qualify they hold identical output,
            so either is valid; the cleanly-keyed one is preferred so repeated
            lookups stay stable.
    """
    return (
        await RepoDocument.find(
            RepoDocument.repo_url == url,
            RepoDocument.repo_hash == commit,
            GT(RepoDocument.user_count, 0),
            Or(
                Eq(RepoDocument.ran_analyzer_version, analyzer_version),
                And(
                    Eq(RepoDocument.ran_analyzer_version, None),
                    Eq(RepoDocument.analyzer_version, analyzer_version),
                ),
            ),
        )
        .sort("-analyzer_version")
        .first_or_none()
    )


async def _find_live_git_repo_by_key(
    url: str, commit: str, analyzer_version: str
) -> RepoDocument | None:
    """Find a live git repo owning the exact `(url, commit, version)` unique key.

    Used only to recover from a `DuplicateKeyError`: the row that won the race
    is defined by the unique index, so this must match `analyzer_version`
    exactly. Resolving it through the version floor would miss a row whose
    worker redeployed mid-run and turn a lost race into a 500.

    Args:
        url(str): The normalized repository URL.
        commit(str): The resolved commit SHA.
        analyzer_version(str): The worker analyzer version.

    Returns:
        RepoDocument | None: The matching repository, or None.
    """
    return await RepoDocument.find_one(
        RepoDocument.repo_url == url,
        RepoDocument.repo_hash == commit,
        RepoDocument.analyzer_version == analyzer_version,
        GT(RepoDocument.user_count, 0),
    )


async def _find_live_zip_repo(
    blob_path: str, analyzer_version: str
) -> RepoDocument | None:
    """Find a live (`user_count > 0`) zip repo whose output is at this version.

    A zip is identified by the archive it was cut from, never by its name:
    `repo_url` holds only a filename two unrelated uploads can share and
    `repo_hash` is unset, whereas `blob_path` is minted per upload by
    `analyze_file` and carried over verbatim by `_relaunch_at_new_version`.
    Those two are its only writers, so the rows sharing one are exactly that
    upload's descendants.

    `repo_hash` is required unset rather than trusting the `user/` prefix: it is
    a fact about the row instead of about a layout built elsewhere, and it is
    the predicate every other reader here (`_dispatch_run`,
    `_resolve_source_ref`) uses to tell the two source shapes apart.

    Matches on the version floor for the reason `_find_live_git_repo` does:
    deduplication asks what a row produced, not the label it was created with.

    Args:
        blob_path(str): The uploaded archive's source prefix, as stored on the
            row — the path without its `.zip` suffix.
        analyzer_version(str): The worker analyzer version.

    Returns:
        RepoDocument | None: The matching repository, or None. When a row whose
            worker redeployed mid-run and a cleanly-keyed one both qualify they
            hold identical output, so either is valid; the cleanly-keyed one is
            preferred so that two holders of one upload relaunching
            independently land on the same row instead of forking it.
    """
    return (
        await RepoDocument.find(
            RepoDocument.blob_path == blob_path,
            Eq(RepoDocument.repo_hash, None),
            GT(RepoDocument.user_count, 0),
            Or(
                Eq(RepoDocument.ran_analyzer_version, analyzer_version),
                And(
                    Eq(RepoDocument.ran_analyzer_version, None),
                    Eq(RepoDocument.analyzer_version, analyzer_version),
                ),
            ),
        )
        .sort("-analyzer_version")
        .first_or_none()
    )


async def _find_live_zip_repo_by_key(
    blob_path: str, analyzer_version: str
) -> RepoDocument | None:
    """Find a live zip repo owning the exact `(blob_path, version)` unique key.

    Used only to recover from a `DuplicateKeyError`: the row that won the race
    is defined by the unique index, so this must match `analyzer_version`
    exactly. Resolving it through the version floor would miss a row whose
    worker redeployed mid-run and turn a lost race into a 500.

    `repo_hash` is left out for the same reason, the key not naming it: this
    asks who owns the key, and a filter the index does not have could exclude
    the very row that raised.

    Args:
        blob_path(str): The uploaded archive's source prefix.
        analyzer_version(str): The worker analyzer version.

    Returns:
        RepoDocument | None: The matching repository, or None.
    """
    return await RepoDocument.find_one(
        RepoDocument.blob_path == blob_path,
        RepoDocument.analyzer_version == analyzer_version,
        GT(RepoDocument.user_count, 0),
    )


async def _join_analysis(
    repo: RepoDocument,
    user: User,
    name: str,
    color: str | None,
    token: str | None = None,
    ref: str | None = None,
) -> tuple[PydanticObjectId, PipelineStatus]:
    """Link the caller to an analysis that already exists and report its status.

    A failed attempt is restarted rather than served: the caller asked for an
    analysis and a cached failure is not one. Failures here are transient
    (network, timeout, expired token) far more often than a property of the
    commit, and this caller brought a token of their own. What must not restart
    is `join_repo`, which asks to see what a holder sees — including a failure
    worth discussing — and so does not come through here.

    The link is made before the restart, so a dispatch that fails leaves the
    caller holding a run they can relaunch rather than unlinked having restarted
    someone else's.

    Args:
        repo(RepoDocument): The repository to join.
        user(User): The authenticated user.
        name(str): The caller's display name for the repository.
        color(str | None): The caller's optional hex color.
        token(str | None): Optional personal or project access token, how a
            private remote is reached. Never persisted, so the caller supplies
            their own; it need not be the one that created the repository.
        ref(str | None): Optional git ref the commit was resolved from, passed
            on to the worker as a fetch hint. Only a caller that just resolved
            the remote has one, which every git caller now is: `RepoDocument`
            does not store the branch, so it cannot be recovered without asking
            the remote.

    Returns:
        tuple[PydanticObjectId, PipelineStatus]: The joined repository's ID and
            its pipeline status. A restart reports PENDING, the status of the
            new attempt, and a repository whose run has not landed yet reports
            PENDING too. In that latter case, its creator links itself before
            inserting its run, so inserting one here would take the claim it is
            about to make.
    """
    await _acquire_repo_link(repo.id, user, name, color)  # type: ignore
    pipeline_run = await _find_latest_pipeline_run(repo.id)  # type: ignore

    if pipeline_run is None:
        return repo.id, PipelineStatus.PENDING  # type: ignore

    if pipeline_run.status == PipelineStatus.FAILED:
        return await _retry_failed_run(repo, pipeline_run, token, ref)

    return repo.id, pipeline_run.status  # type: ignore


def _parsed_version(value: str) -> Version | None:
    """Parse an analyzer version, or None when it is not a version string.

    The version is an opaque string as far as this backend is concerned:
    nothing validates its shape where it is fetched, and a worker is free to
    report a build ID instead. Parsing is therefore never load-bearing — a
    caller that cannot order two versions compares them as strings, the answer
    it had before either was parsed.

    Args:
        value(str): The analyzer version to parse.

    Returns:
        Version | None: The parsed version, or None when `value` does not
            describe one.
    """
    try:
        return Version(value)
    except InvalidVersion:
        return None


def _is_behind(value: str, current_analyzer_version: str) -> bool:
    """Report whether an analyzer version is older than the worker's current one.

    Behind means older rather than merely different, so a worker that has
    rolled back is not behind what it already analyzed. Ordering also settles
    the spellings of a single version (`1.4.0`, `v1.4.0`) that differ as strings
    and not at all as versions.

    Versions that do not parse fall back to inequality, the answer this gave
    when nothing was parsed. Two identical opaque versions are therefore not
    behind one another, and no read path can be made to raise by what the worker
    reports.

    Args:
        value(str): The analyzer version to judge.
        current_analyzer_version(str): The worker's current analyzer version.

    Returns:
        bool: True if `value` is older than the worker's current version.
    """
    parsed_value = _parsed_version(value)
    parsed_current = _parsed_version(current_analyzer_version)

    if parsed_value is None or parsed_current is None:
        return value != current_analyzer_version

    return parsed_value < parsed_current


async def _relaunch_at_current_version(
    repo: RepoDocument,
    token: str | None,
    ref: str | None,
) -> tuple[PydanticObjectId, PipelineStatus]:
    """Re-run a repository that already holds the current version's output.

    A repository is at the current analyzer version if its output is stamped
    with it, or if the worker has not stamped one yet and the repository was
    created with it — the version floor answers both. A repository ahead of
    the worker arrives here too, a rollback having left it holding output no
    version on offer can reproduce.

    There is no newer analysis to move onto, so the only relaunches left are
    repairs, and both act on this repository rather than creating one. A create
    that died before inserting its run is claimed and dispatched; its owner is
    the only one who can reach it, no join path sees it, so the relaunch they
    asked for is that repair. A failed attempt is retried beside the one that
    failed, which is safe because a failed run has no artifacts to protect.
    Anything else is refused: a completed analysis is what was asked for, and an
    in-flight one is already producing it.

    Args:
        repo(RepoDocument): The repository to re-run.
        token(str | None): Optional personal or project access token, how a
            private remote is reached. Never persisted, so the caller supplies
            their own; it need not be the one that created the repository.
        ref(str | None): Optional git ref the commit was resolved from, passed
            on to the worker as a fetch hint.

    Returns:
        tuple[PydanticObjectId, PipelineStatus]: This repository's own ID —
            nothing is created here, which is how a caller tells a retry from a
            new entry — and its pipeline status.

    Raises:
        HTTPException: 409 if the repository already holds this version's
            output, or an analysis of it is already in progress — including a
            wedged run that `reconcile_pipeline_run` could not settle, which a
            later relaunch clears once the worker answers.
    """
    pipeline_run = await _find_latest_pipeline_run(repo.id)  # type: ignore

    if pipeline_run is None:
        repair = await _claim_next_run(repo.id)  # type: ignore
        if repair is not None:
            await _dispatch_run(repo, repair, token, ref)

        return repo.id, PipelineStatus.PENDING  # type: ignore

    if pipeline_run.status == PipelineStatus.FAILED:
        return await _retry_failed_run(repo, pipeline_run, token, ref)

    if pipeline_run.status == PipelineStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repository is already at the current analyzer version.",
        )

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="An analysis of this repository is already in progress.",
    )


async def _relaunch_at_new_version(
    repo: RepoDocument,
    user_repo: UserRepoDocument,
    user: User,
    analyzer_version: str,
    token: str | None,
    ref: str | None,
) -> tuple[PydanticObjectId, PipelineStatus]:
    """Move a caller onto an analysis of their repository at a newer version.

    Nothing is replaced: the caller's existing repository stays listed and keeps
    its artifacts, and this adds a link to a second one at the current version.

    An equivalent analysis is joined when one already exists, whichever shape
    the source is. Only the key differs. A git analysis is keyed on the commit,
    which any caller can name. A zip is keyed on `blob_path`, the archive it was
    cut from: `repo_url` holds only a filename, which two unrelated uploads can
    share, and `repo_hash` is unset, so neither identifies it, while the upload
    does and carries over verbatim, no second copy of the archive existing to
    point at.

    Keying a zip on it is what makes a second relaunch of one upload land on the
    row the first one created rather than mint another: the caller already holds
    that row, so `_join_analysis` refuses with 409, the answer git has always
    given. Skipping the lookup instead created a row per call, and a user's list
    grew one entry per click with nothing to tell them apart.

    Reaching another holder's row this way is the point and not a leak. To be
    here the caller holds a row with this `blob_path`, and the only rows sharing
    one are that upload's own descendants — the same bytes, analyzed at a
    version they asked for. Two holders of one upload thereby pay for one
    analysis between them, as two holders of one commit already do.

    Args:
        repo(RepoDocument): The repository being left behind, read for the
            source address and language filter to carry over.
        user_repo(UserRepoDocument): The caller's link to it, supplying the
            display name and color to carry over.
        user(User): The authenticated user.
        analyzer_version(str): The worker's current analyzer version.
        token(str | None): Optional personal or project access token, how a
            private remote is reached. Never persisted, so the caller supplies
            their own; it need not be the one that created the repository.
        ref(str | None): Optional git ref the commit was resolved from, passed
            on to the worker as a fetch hint.

    Returns:
        tuple[PydanticObjectId, PipelineStatus]: The resulting repository ID,
            always a different one from the caller's — theirs is stale by
            definition, so the version floor the lookups match on cannot select
            it — and its pipeline status.
    """
    commit = repo.repo_hash

    if commit is None:
        blob_path = repo.blob_path
        existing = await _find_live_zip_repo(blob_path, analyzer_version)
    else:
        blob_path = _git_blob_path(repo.repo_url, commit)
        existing = await _find_live_git_repo(repo.repo_url, commit, analyzer_version)

    if existing is None:
        new_repo = RepoDocument(
            repo_url=repo.repo_url,
            repo_hash=commit,
            analyzer_version=analyzer_version,
            blob_path=blob_path,
            languages=repo.languages,
        )
        try:
            await _start_new_analysis(
                new_repo, user, user_repo.name, user_repo.color, token, ref
            )
        except DuplicateKeyError:
            # Lost the race, join the winner identified by the unique key
            existing = (
                await _find_live_zip_repo_by_key(blob_path, analyzer_version)
                if commit is None
                else await _find_live_git_repo_by_key(
                    repo.repo_url, commit, analyzer_version
                )
            )
            if existing is None:
                raise
        else:
            return new_repo.id, PipelineStatus.PENDING  # type: ignore

    return await _join_analysis(
        existing, user, user_repo.name, user_repo.color, token, ref
    )


async def _resolve_source_ref(repo: RepoDocument, token: str | None) -> str | None:
    """Gate a relaunch on source access, recovering the ref to fetch from.

    Relaunching is what makes the analysis fetch the remote again, so it takes
    access to the remote — reading artifacts the system already holds and making
    it fetch from someone else's private server are different permissions, and
    only the second needs proof. The gate is unconditional rather than limited
    to callers supplying a token: gating on the token would make a stale one
    worse than none, and gating on whether the source is still cached would make
    the answer depend on state no caller can see.

    A zip has no remote to reach, so there is nothing to gate and no ref to
    recover. The source shape is read off the repository rather than passed in,
    the same way `_dispatch_run` reads it: a zip row leaves `repo_hash` unset.

    Resolving is also the only chance to recover the branch, which
    `RepoDocument` does not store, and which the worker would otherwise fetch
    without.

    Args:
        repo(RepoDocument): The repository whose source must still be reachable.
        token(str | None): Optional personal or project access token, how a
            private remote is reached.

    Returns:
        str | None: The branch the commit was resolved from, or None for a zip
            and for a bare mid-history commit no branch tip matches.
    """
    if repo.repo_hash is None:
        return None

    resolved = await resolve_git_ref(
        repo_url=repo.repo_url,
        commit=repo.repo_hash,
        token=token,
    )

    return resolved.ref


async def _retry_failed_run(
    repo: RepoDocument,
    pipeline_run: PipelineRunDocument,
    token: str | None,
    ref: str | None = None,
) -> tuple[PydanticObjectId, PipelineStatus]:
    """Re-dispatch a failed analysis on the repository that owns it.

    Reuses the repository instead of creating one: the "nothing is ever replaced"
    rule protects artifacts, and a failed run has none.

    The failed attempt is not overwritten but stamped `retried_at` beside a new
    run, so its `status` and `meta` stay readable. Claiming (`_claim_next_run`)
    before stamping is what makes concurrent retries dispatch once: the successor
    is the claim, so stamping first could leave a repository marked as retried
    with nothing retrying it. `retried_at` is audit trail only.

    Args:
        repo(RepoDocument): The repository whose analysis failed.
        pipeline_run(PipelineRunDocument): Its failed pipeline run.
        token(str | None): Optional personal or project access token, how a
            private remote is reached. Never persisted, so the caller supplies
            their own; it need not be the one that created the repository.
        ref(str | None): Optional git ref the commit was resolved from, passed
            on to the worker as a fetch hint. Only a caller that just resolved
            the remote has one, which every git caller now is: `RepoDocument`
            does not store the branch, so it cannot be recovered without asking
            the remote.

    Returns:
        tuple[PydanticObjectId, PipelineStatus]: The unchanged repository ID —
            how a caller tells a retry from a new entry — and its pipeline
            status.
    """
    retry = await _claim_next_run(repo.id)  # type: ignore

    if retry is None:
        return repo.id, PipelineStatus.PENDING  # type: ignore

    await PipelineRunDocument.find_one(
        PipelineRunDocument.id == pipeline_run.id,
        Eq(PipelineRunDocument.retried_at, None),
    ).update(
        Set(  # type: ignore[no-untyped-call]
            {PipelineRunDocument.retried_at: datetime.now(UTC)}
        )
    )

    await _dispatch_run(repo, retry, token, ref)

    return repo.id, PipelineStatus.PENDING  # type: ignore


async def _start_new_analysis(
    repo: RepoDocument,
    user: User,
    name: str,
    color: str | None,
    token: str | None = None,
    ref: str | None = None,
) -> None:
    """Persist a new repository, link its first holder, and dispatch its run.

    The repository is inserted first and alone, because it is the only write
    here that can collide: it carries the content-addressed unique key, so a
    `DuplicateKeyError` from this call means a concurrent caller won the race
    and nothing was written. The link and the run are keyed to an ID that came
    into existence a moment earlier, so neither can collide in turn.

    Dispatching last is what makes a create recoverable: `start_orchestration`
    marks its own run FAILED before raising, dropping it out of the partial
    unique index, so an unreachable worker leaves a repository that can be
    relaunched rather than one wedged behind a pending run forever.

    Args:
        repo(RepoDocument): The unsaved repository to create.
        user(User): The authenticated user, its first holder.
        name(str): The caller's display name for the repository.
        color(str | None): The caller's optional hex color.
        token(str | None): Optional personal or project access token, how a
            private remote is reached. Never persisted, so the caller supplies
            their own; it need not be the one that created the repository.
        ref(str | None): Optional git ref the commit was resolved from, passed
            on to the worker as a fetch hint. Only a caller that just resolved
            the remote has one, which every git caller now is: `RepoDocument`
            does not store the branch, so it cannot be recovered without asking
            the remote.
    """
    await repo.insert()

    user_repo = UserRepoDocument(
        user_id=user.uid,
        repo_id=repo.id,
        name=name,
        color=color,
    )
    await user_repo.insert()

    pipeline_run = PipelineRunDocument(repo_id=repo.id)
    await pipeline_run.insert()

    await _dispatch_run(repo, pipeline_run, token, ref)


async def analyze_file(
    filename: str,
    file_data: BinaryIO,
    name: str,
    languages: list[str],
    user: User,
    color: str | None = None,
) -> PydanticObjectId:
    """Validate, upload, and start analysis of a ZIP file.

    Args:
        filename(str): The original filename of the uploaded file.
        file_data(BinaryIO): The file-like object containing the ZIP data.
        name(str): The display name for the repository.
        languages(list[str]): The languages to analyze (empty = all supported).
        user(User): The authenticated user submitting the file.
        color(str | None): Optional hex color for the repository.

    Returns:
        PydanticObjectId: The newly created RepoDocument ID as a string.

    Raises:
        HTTPException: 400 if the file is not a ZIP file.
    """
    repo_name = filename
    if not repo_name or not repo_name.endswith(".zip"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .zip files are accepted.",
        )

    # Resolve the analyzer version first to avoid unnecessary uploads if the worker
    # is unreachable
    analyzer_version = await get_analyzer_version()

    # Generate a unique blob name and upload the file to Azure Blob Storage
    blob_name = f"user/{user.uid}/{uuid4()}/src/{repo_name}"

    container_client = get_container_client()
    await container_client.upload_blob(name=blob_name, data=file_data, overwrite=True)

    # Create RepoDocument, UserRepoDocument, and PipelineRunDocument in MongoDB
    repo = RepoDocument(
        repo_url=repo_name,  # Store the original filename as repo_url for reference
        blob_path=blob_name.removesuffix(".zip"),
        analyzer_version=analyzer_version,
        languages=languages,
    )
    await repo.insert()

    user_repo = UserRepoDocument(
        user_id=user.uid,
        repo_id=repo.id,
        name=name,
        color=color,
    )
    await user_repo.insert()

    pipeline_run = PipelineRunDocument(
        repo_id=repo.id,
    )
    await pipeline_run.insert()

    # Start the analysis orchestration asynchronously
    await start_orchestration(blob_name, languages, pipeline_run)

    return repo.id  # type: ignore


async def analyze_git(
    repo_url: str,
    branch: str | None,
    commit: str | None,
    token: str | None,
    name: str,
    user: User,
    color: str | None = None,
) -> tuple[PydanticObjectId, PipelineStatus, bool]:
    """Resolve, deduplicate, and start analysis of a git repository.

    Verifies the remote is accessible and pins a concrete commit, then either
    joins the caller to an existing analysis of the same (url, commit, analyzer
    version) or starts a new one. Git repositories always analyze all supported
    languages (a shared artifact), so no language filter is accepted.

    Joining an analysis whose run failed restarts it rather than reporting the
    failure: this call asked for an analysis and a failed run is not one, and
    failures here are transient (network, timeout, expired token) far more often
    than a property of the commit. The link is made before the restart, so a
    dispatch failure leaves the caller linked to a run they can relaunch rather
    than unlinked having restarted someone else's.

    Joining a repository with no run yet reports PENDING rather than inserting
    one: the creator links itself before inserting its run, so a repository
    reachable without one is mid-create, and inserting here would take the claim
    it is about to make and leave its dispatch skipped.

    Args:
        repo_url(str): The git repository URL.
        branch(str | None): Optional branch to resolve the commit from.
        commit(str | None): Optional explicit commit SHA.
        token(str | None): Optional personal or project access token for a private
            remote.
        name(str): The display name for the repository.
        user(User): The authenticated user.
        color(str | None): Optional hex color for the repository.

    Returns:
        tuple[PydanticObjectId, PipelineStatus, bool]: The repo ID, its pipeline
            status, and whether the caller joined an existing analysis. A
            restarted join reports PENDING, the status of the new attempt.

    Raises:
        HTTPException: 422 if the URL is malformed, the remote is inaccessible,
            the branch does not exist, or the commit is malformed. 502 if the
            remote or the worker is unreachable. 409 if the caller already has
            this repository.
    """
    resolved = await resolve_git_ref(
        repo_url=repo_url,
        branch=branch,
        commit=commit,
        token=token,
    )
    analyzer_version = await get_analyzer_version()

    existing = await _find_live_git_repo(
        resolved.url, resolved.commit, analyzer_version
    )

    if existing is None:
        repo = RepoDocument(
            repo_url=resolved.url,
            repo_hash=resolved.commit,
            analyzer_version=analyzer_version,
            blob_path=_git_blob_path(resolved.url, resolved.commit),
            languages=[],
        )
        try:
            await _start_new_analysis(repo, user, name, color, token, resolved.ref)
        except DuplicateKeyError:
            # Lost the race, join the winner identified by the unique key
            existing = await _find_live_git_repo_by_key(
                resolved.url, resolved.commit, analyzer_version
            )
            if existing is None:
                raise
        else:
            return repo.id, PipelineStatus.PENDING, False  # type: ignore

    # An existing live analysis was found (up-front or after losing the race):
    # join it and report its current pipeline status
    repo_id, run_status = await _join_analysis(
        existing, user, name, color, token, resolved.ref
    )

    return repo_id, run_status, True


async def relaunch_repo(
    repo: RepoDocument,
    user_repo: UserRepoDocument,
    user: User,
    token: str | None = None,
) -> tuple[PydanticObjectId, PipelineStatus]:
    """Re-analyze a repository with the worker's current analyzer version.

    Also how a failed analysis is retried, which is not a second feature: the
    caller asks for output at the current version, and whether producing it
    takes a new analysis or a re-run of one that failed is a fact about this
    repository rather than about the request.

    Whether a newer version than this repository's output exists is what
    decides between them, and it is `is_stale` that decides it — the same
    predicate the API reports as the `stale` flag, so the badge a caller acts
    on and the branch their relaunch takes cannot disagree. Asking for
    *newer* rather than *different* is what keeps a rolled-back worker from
    sending a repository down `_relaunch_at_new_version`, which would create a
    second entry holding older output than the one the caller already has.
    `_relaunch_at_new_version` moves the caller onto an analysis at the newer
    one, adding a link and leaving their existing repository listed, theirs to
    drop via `delete_repo`.
    `_relaunch_at_current_version` has nowhere newer to go, so every relaunch
    left is a repair of this repository where it stands: a failed run is
    retried beside the one that failed, a run that was never inserted is
    claimed and dispatched, and anything else is refused, a completed analysis
    being what was asked for and an in-flight one already producing it.

    The order of what comes first is load-bearing. `_resolve_source_ref` gates
    on remote access ahead of the version branch and every write, token or not,
    so a caller who cannot reach the remote is refused before anything is
    written. `reconcile_pipeline_run` sits below the version fetch, so a
    request that aborts there writes nothing, and above the branch, so a wedged
    run is settled even when this call will not re-dispatch it. Settling one
    writes FAILED, which is why nothing here needs a case for it: a run the
    worker abandoned arrives at the retry as an ordinary failure.

    Args:
        repo(RepoDocument): The repository to re-analyze.
        user_repo(UserRepoDocument): The caller's link to it, supplying the
            display name and color to carry over.
        user(User): The authenticated user.
        token(str | None): Optional personal or project access token, how a
            private remote is reached. Verified here before dispatch, then used
            by the worker for its clone. Never persisted, so a caller supplies
            their own; it need not be the one that created the repository.

    Returns:
        tuple[PydanticObjectId, PipelineStatus]: The resulting repository ID —
            a new one, or the caller's own when a failed run is retried in
            place — and its pipeline status.
    """
    ref = await _resolve_source_ref(repo, token)
    analyzer_version = await get_analyzer_version()

    await reconcile_pipeline_run(repo.id)  # type: ignore

    if not is_stale(repo, analyzer_version):
        return await _relaunch_at_current_version(repo, token, ref)

    return await _relaunch_at_new_version(
        repo, user_repo, user, analyzer_version, token, ref
    )


async def create_repo(
    filename: str | None,
    file_data: BinaryIO | None,
    repo_url: str | None,
    branch: str | None,
    commit: str | None,
    token: str | None,
    name: str,
    languages: list[str],
    user: User,
    color: str | None = None,
) -> tuple[PydanticObjectId, PipelineStatus, bool]:
    """Validate a create request and dispatch to the zip or git analysis path.

    Exactly one of `file_data` (zip upload) or `repo_url` (git) must be provided.
    Zip uploads honor the `languages` filter and are personal. Git analyses are
    shared (deduplicated by commit + analyzer version), always analyze all
    languages, and join an existing analysis when one already exists.

    Args:
        filename(str | None): The uploaded file's name (zip path only).
        file_data(BinaryIO | None): The uploaded file stream (zip path only).
        repo_url(str | None): A git repository URL (alternative to a file).
        branch(str | None): Optional branch to resolve the commit from.
        commit(str | None): Optional explicit commit SHA.
        token(str | None): Optional personal or project access token for a private
            remote (git path only).
        name(str): The display name for the repository.
        languages(list[str]): The languages to analyze (zip only. empty = all
            supported).
        user(User): The authenticated user.
        color(str | None): Optional hex color for the repository.

    Returns:
        tuple[PydanticObjectId, PipelineStatus, bool]: The repository ID, its
            pipeline status, and whether the caller joined an existing analysis
            (`True` only on a git join).

    Raises:
        HTTPException: 400 if the source combination is invalid (neither or both
            sources, git-only fields on a zip, or a language filter on git). 422
            if a zip request asks for a language the worker does not support, or
            if a git request's URL/branch/commit cannot be resolved. 502 if the
            worker or the git remote is unreachable.
    """
    if file_data is not None and repo_url is None:
        # Zip upload path
        if branch is not None or commit is not None or token is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="branch/commit/token are only valid with a repo_url.",
            )
        if not filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Filename is required",
            )
        await validate_languages(languages)
        repo_id = await analyze_file(
            filename=filename,
            file_data=file_data,
            name=name,
            languages=languages,
            user=user,
            color=color,
        )
        return repo_id, PipelineStatus.PENDING, False

    if repo_url is not None and file_data is None:
        # Git path — a shared artifact + language filters do not apply
        if languages:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Language filters are not supported for git repositories: "
                "All supported languages are analyzed.",
            )
        return await analyze_git(
            repo_url=repo_url,
            branch=branch,
            commit=commit,
            token=token,
            name=name,
            user=user,
            color=color,
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Provide exactly one of a zip file or a repo_url.",
    )


async def delete_repo(user_repo: UserRepoDocument, repo: RepoDocument) -> None:
    """Unlink a repository from the user and decrement its user count.

    Deletes children (threads, analyses) before the parent link to ensure
    partial failures leave the system in a retryable state. If the user
    count reaches zero, the repository is considered garbage and should be
    cleaned up by a separate job. Orphaned messages and checkpointer
    data should also be handled by a separate job.

    Args:
        user_repo(UserRepoDocument): The user-repo link.
        repo(RepoDocument): The repository document.
    """
    await delete_threads_by_repo(user_repo.user_id, user_repo.repo_id)
    await delete_analyses_by_repo(user_repo.user_id, user_repo.repo_id)
    await user_repo.delete()
    await repo.update(Inc({RepoDocument.user_count: -1}))  # type: ignore[no-untyped-call]


async def get_file_documentation(
    repo_id: PydanticObjectId, file_id: PydanticObjectId
) -> FileDocumentationDocument:
    """Retrieve the documentation for a specific file in a repository.

    Args:
        repo_id(PydanticObjectId): The RepoDocument ID.
        file_id(PydanticObjectId): The FileDocument ID.

    Returns:
        FileDocumentationDocument: The documentation for the file.

    Raises:
        HTTPException: 404 if the documentation is not found.
    """
    documentation = await FileDocumentationDocument.find_one(
        FileDocumentationDocument.repo_id == repo_id,
        FileDocumentationDocument.file_id == file_id,
    )
    if documentation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No documentation found for this file.",
        )
    return documentation


async def get_file_graphs(
    repo_id: PydanticObjectId, file_id: PydanticObjectId
) -> tuple[ASTDocument | None, list[CFGDocument], list[DFGDocument]]:
    """Retrieve the AST, CFG, and DFG graphs for a specific file.

    Args:
        repo_id(PydanticObjectId): The RepoDocument ID.
        file_id(PydanticObjectId): The FileDocument ID.

    Returns:
        tuple[ASTDocument | None, list[CFGDocument], list[DFGDocument]]:
            The AST graph (or None), and lists of CFG and DFG graphs (one per scope).
    """
    ast = await ASTDocument.find_one(
        ASTDocument.repo_id == repo_id,
        ASTDocument.file_id == file_id,
    )
    cfgs = await CFGDocument.find(
        CFGDocument.repo_id == repo_id,
        CFGDocument.file_id == file_id,
    ).to_list()
    dfgs = await DFGDocument.find(
        DFGDocument.repo_id == repo_id,
        DFGDocument.file_id == file_id,
    ).to_list()

    return ast, cfgs, dfgs


async def get_file_source(repo: RepoDocument, file_doc: FileDocument) -> str:
    """Retrieve the source content of a file from blob storage.

    Args:
        repo(RepoDocument): The verified repository document.
        file_doc(FileDocument): The verified file document.

    Returns:
        str: The file content.
    """
    blob_path = f"{repo.blob_path}/{file_doc.path}"
    container_client = get_container_client()
    downloader = await container_client.download_blob(blob_path)
    raw = await downloader.readall()
    return raw.decode(errors="replace")


async def get_files(repo_id: PydanticObjectId) -> list[FileDocument]:
    """Retrieve all files belonging to a repository.

    Args:
        repo_id(PydanticObjectId): The RepoDocument ID.

    Returns:
        list[FileDocument]: All FileDocuments associated with the repository.
    """
    files = await FileDocument.find(
        FileDocument.repo_id == repo_id,
    ).to_list()
    return files


async def get_pipeline_history(
    repo_id: PydanticObjectId,
) -> list[PipelineRunDocument]:
    """Retrieve every pipeline run for a repository, newest attempt first.

    A retry supersedes an attempt rather than overwriting it, so a repository
    holds one run per attempt and the first element is the current one. Ordered
    by `_id`, whose leading timestamp makes insertion order the attempt order.

    Args:
        repo_id(PydanticObjectId): The RepoDocument ID.

    Returns:
        list[PipelineRunDocument]: The repository's runs, newest first. Empty
            when the repository has none.
    """
    return (
        await PipelineRunDocument.find(PipelineRunDocument.repo_id == repo_id)
        .sort("-_id")
        .to_list()
    )


async def get_pipeline_with_attempts(
    repo_id: PydanticObjectId,
) -> tuple[PipelineRunDocument, list[PipelineRunDocument]]:
    """Retrieve a repository's current pipeline run and every attempt on it.

    The read path's counterpart to `_find_latest_pipeline_run`: a reader reports
    the current attempt and the ones before it, so both come from one call
    rather than from the caller knowing that `get_pipeline_history` orders
    newest first. No runs is a 404 here — `status` has no honest value to report
    — while `get_pipeline_history` stays a plain query that answers with an
    empty list.

    Args:
        repo_id(PydanticObjectId): The RepoDocument ID.

    Returns:
        tuple[PipelineRunDocument, list[PipelineRunDocument]]: The latest run,
            and every run newest first. The list is never empty and its first
            element is the latest run.

    Raises:
        HTTPException: 404 if the repository has no pipeline run.
    """
    pipeline_runs = await get_pipeline_history(repo_id)

    if not pipeline_runs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pipeline run found for this repository.",
        )

    return pipeline_runs[0], pipeline_runs


async def get_repo_meta(
    repo_id: PydanticObjectId,
) -> RepoMetaDocument | None:
    """Retrieve the meta document for a repository.

    Args:
        repo_id(PydanticObjectId): The RepoDocument ID.

    Returns:
        RepoMetaDocument | None: The meta document, or None if it doesn't exist.
    """
    return await RepoMetaDocument.find_one(
        RepoMetaDocument.repo_id == PydanticObjectId(repo_id),
    )


async def get_repos(
    user: User, search: str | None = None
) -> list[tuple[RepoDocument, UserRepoDocument]]:
    """Retrieve all repositories belonging to the user.

    Args:
        user(User): The authenticated user.
        search(str | None): Optional search string for case-insensitive
            substring match on the repository display name.

    Returns:
        list[tuple[RepoDocument, UserRepoDocument]]: A list of
            (RepoDocument, UserRepoDocument) tuples.
    """
    query = UserRepoDocument.find(UserRepoDocument.user_id == user.uid)
    if search is not None:
        query = query.find({"name": {"$regex": re.escape(search), "$options": "i"}})
    user_repos = await query.sort("-_id").to_list()

    if not user_repos:
        return []

    repo_ids = [ur.repo_id for ur in user_repos]
    repos = await RepoDocument.find(In(RepoDocument.id, repo_ids)).to_list()
    repo_map = {repo.id: repo for repo in repos}
    return [(repo_map[ur.repo_id], ur) for ur in user_repos if ur.repo_id in repo_map]


def is_stale(repo: RepoDocument, current_analyzer_version: str) -> bool:
    """Report whether relaunching a repository would yield newer output.

    A call to action rather than a provenance report, so every version a
    repository names has to be behind the worker. `ran_analyzer_version` bounds
    what it already holds, once the worker has stamped one; `analyzer_version`
    bounds what it may become, being its key in both partial unique indexes — a
    repository already carrying the current version owns the only row that
    version admits, and artifacts keyed by `repo_id` and written insert-only
    cannot be rebuilt in place either. Calling that stale only sends the
    relaunch off to build a second repository whose insert collides with it.

    The two disagree exactly when a redeploy lands between the ask and the
    start, and that drift resolves at the worker's next version, which puts both
    behind again and frees the row a relaunch needs. Behind is ordered rather
    than merely different (`_is_behind`), so a rolled-back worker is not newer
    than what it already analyzed.

    Args:
        repo(RepoDocument): The repository to judge.
        current_analyzer_version(str): The worker's current analyzer version.

    Returns:
        bool: True if the worker has moved on from this repository and can be
            asked for a newer analysis of it.
    """
    stamped = repo.ran_analyzer_version

    return _is_behind(repo.analyzer_version, current_analyzer_version) and (
        stamped is None or _is_behind(stamped, current_analyzer_version)
    )


async def join_repo(repo_id: PydanticObjectId, user: User) -> str:
    """Join an existing repository, adding it to the current user's list.

    Claims a holder's slot and links the caller through `_acquire_repo_link`.
    The caller supplies no display name, so `repo_url` is used verbatim. Neither
    it nor the colour is copied from another link: both are how a holder
    organises their own list, so copying one would hand over a stranger's
    wording and depend on which link the store happened to return.

    Args:
        repo_id(PydanticObjectId): The RepoDocument ID to join.
        user(User): The authenticated user.

    Returns:
        str: The repository's URL, which is the caller's display
            name when joining.

    Raises:
        HTTPException: 409 if the user already has this repository.
        HTTPException: 404 if the repository does not exist or is
            no longer available (user_count <= 0).
    """
    repo = await RepoDocument.find_one(
        RepoDocument.id == repo_id,
        GT(RepoDocument.user_count, 0),
    )
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found.",
        )
    name = repo.repo_url

    await _acquire_repo_link(repo_id, user, name, None)

    return name


async def update_user_repo(
    user_repo: UserRepoDocument,
    color: str | None,
    name: str | None,
    fields_set: set[str],
) -> UserRepoDocument:
    """Update per-user metadata for a repository.

    Only fields present in *fields_set* are applied. Omitted fields are left
    unchanged; `color` sent as `null` clears the value, while `name`
    sent as `null` is ignored (name is required).

    Args:
        user_repo(UserRepoDocument): The user-repo link to update.
        color(str | None): The hex color string, or None to clear.
        name(str | None): The display name, or None to leave unchanged.
        fields_set(set[str]): Fields explicitly provided by the client
            (from `UpdateUserRepoRequest.model_fields_set`).

    Returns:
        UserRepoDocument: The updated user-repo document.
    """
    if "name" in fields_set and name is not None:
        user_repo.name = name
    if "color" in fields_set:
        user_repo.color = color
    await user_repo.save()
    return user_repo
