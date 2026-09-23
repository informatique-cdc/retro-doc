"""Pipeline utilities.

This module defines utility functions for the pipeline blueprint.
"""

from __future__ import annotations

import asyncio
import os
import queue
from collections.abc import Generator
from pathlib import Path

from azure.storage.blob.aio import ContainerClient, StorageStreamDownloader
from loguru import logger
from stream_unzip import stream_unzip

_SENTINEL_SUFFIX_MATERIALIZED = ".done"
"""Marks a prefix as fully materialized. A sibling of the prefix (i.e. listing
`f"{prefix}/"`) never returns it.
"""


def _drain_queue(chunk_q: queue.Queue[bytes | None]) -> Generator[bytes]:
    """Yield chunks from a thread-safe queue until a `None` flag is
    encountered, indicating the end of the stream.

    Args:
        chunk_q(queue.Queue[bytes | None]): The queue to drain.

    Returns:
        Generator[bytes, None, None]: Yields byte chunks until `None` is received.
    """
    while True:
        c = chunk_q.get()
        if c is None:
            return
        yield c


async def mark_materialized(container: ContainerClient, prefix: str) -> None:
    """Mark every file under `prefix` as uploaded.

    Called only once the last file is up, so a run that dies mid-upload leaves
    no marker and the next one materializes the source again.

    Args:
        container(ContainerClient): The Azure Blob Storage container client.
        prefix(str): The blob storage prefix the source was uploaded under.
    """
    await container.upload_blob(
        f"{prefix}{_SENTINEL_SUFFIX_MATERIALIZED}", b"", overwrite=True
    )


async def reuse_materialized(container: ContainerClient, prefix: str) -> list[str]:
    """List the files a previous run already uploaded under `prefix`.

    Only a prefix whose content is fixed by its path may be reused this way: a
    git checkout is addressed by its commit, a zip upload by the one-off id the
    backend mints for it, so neither can ever hold two different trees.

    Args:
        container(ContainerClient): The Azure Blob Storage container client.
        prefix(str): The blob storage prefix the source was uploaded under.

    Returns:
        list[str]: The blob paths to reuse, empty when the source has to be
            materialized again.
    """
    if not await container.get_blob_client(
        f"{prefix}{_SENTINEL_SUFFIX_MATERIALIZED}"
    ).exists():
        return []

    reused = sorted(
        [
            name
            async for name in container.list_blob_names(name_starts_with=f"{prefix}/")
        ]
    )
    if not reused:
        logger.warning(
            f"Pipeline: '{prefix}{_SENTINEL_SUFFIX_MATERIALIZED}' has no content, "
            "materializing again"
        )

    return reused


async def stream_download(
    stream: StorageStreamDownloader[bytes],
    chunk_q: queue.Queue[bytes | None],
) -> None:
    """Stream the blob content in chunks and put them into a thread-safe
    queue for processing.

    Args:
        stream(StorageStreamDownloader[bytes]): The blob stream to read from.
        chunk_q(queue.Queue[bytes | None]): The queue to put the chunks into.
    """
    loop = asyncio.get_running_loop()
    async for chunk in stream.chunks():
        await loop.run_in_executor(None, chunk_q.put, chunk)
    await loop.run_in_executor(None, chunk_q.put, None)


def stream_extract(
    chunk_q: queue.Queue[bytes | None],
    file_q: asyncio.Queue[tuple[str, bytes] | None],
    loop: asyncio.AbstractEventLoop,
    extracted_prefix: str,
) -> None:
    """Decompress the zip stream in a separate thread, putting every real
    file into an async queue for upload.

    Args:
        chunk_q(queue.Queue[bytes | None]): The queue to read the zip chunks from.
        file_q(asyncio.Queue[tuple[str, bytes] | None]): The queue to put
            extracted files into as (path, data) tuples.
        loop(asyncio.AbstractEventLoop): The event loop to use for thread-to-async communication
        extracted_prefix(str): The prefix to prepend to extracted file paths when uploading.
    """
    try:
        for name_bytes, _size, file_chunks in stream_unzip(_drain_queue(chunk_q)):
            name = name_bytes.decode("utf-8")

            if name.endswith("/") or name.startswith("__MACOSX"):
                for _ in file_chunks:
                    pass
                continue

            path = f"{extracted_prefix}/{name}"
            data = b"".join(file_chunks)
            asyncio.run_coroutine_threadsafe(file_q.put((path, data)), loop).result()
    except Exception:
        # Keep draining the chunks, or stream_download stays blocked on a full
        # chunk_q and the activity could hang until functionTimeout
        for _ in _drain_queue(chunk_q):
            pass
        raise
    finally:
        # Always close the queue, or a failed walk leaves stream_upload waiting
        asyncio.run_coroutine_threadsafe(file_q.put(None), loop).result()


async def stream_upload(
    file_q: asyncio.Queue[tuple[str, bytes] | None],
    container: ContainerClient,
) -> list[str]:
    """Upload each extracted file from the async queue to blob storage, returning a list
    of the uploaded blob paths.

    Args:
        file_q(asyncio.Queue[tuple[str, bytes] | None]): The queue to read
            extracted files from as (path, data) tuples.
        container(ContainerClient): The Azure Blob Storage container client to use for uploads.

    Returns:
        list[str]: A list of blob paths for the uploaded files.
    """
    uploaded_paths: list[str] = []
    while True:
        item = await file_q.get()
        if item is None:
            break
        path, data = item
        await container.upload_blob(path, data, overwrite=True)
        uploaded_paths.append(path)
    return uploaded_paths


def stream_walk(
    root: Path,
    file_q: asyncio.Queue[tuple[str, bytes] | None],
    loop: asyncio.AbstractEventLoop,
    checkout_prefix: str,
) -> None:
    """Walk a checked out working tree in a separate thread, putting every real
    file into an async queue for upload.

    Args:
        root(Path): The root of the working tree to walk.
        file_q(asyncio.Queue[tuple[str, bytes] | None]): The queue to put files
            into as (path, data) tuples.
        loop(asyncio.AbstractEventLoop): The event loop to use for thread-to-async communication
        checkout_prefix(str): The prefix to prepend to file paths when uploading.
    """
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune the git metadata before descending into it: it is not
            # source, and every file the pipeline sees gets cataloged
            dirnames[:] = [d for d in dirnames if d != ".git"]

            for filename in filenames:
                file_path = Path(dirpath) / filename

                # A symlink points outside the tree or loops back into it
                if file_path.is_symlink():
                    continue

                path = f"{checkout_prefix}/{file_path.relative_to(root).as_posix()}"
                data = file_path.read_bytes()
                asyncio.run_coroutine_threadsafe(
                    file_q.put((path, data)), loop
                ).result()
    finally:
        # Always close the queue, or a failed walk leaves stream_upload waiting
        asyncio.run_coroutine_threadsafe(file_q.put(None), loop).result()
