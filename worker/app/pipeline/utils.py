"""Pipeline utilities.

This module defines utility functions for the pipeline blueprint.
"""

from __future__ import annotations

import asyncio
import queue
from collections.abc import Generator

from azure.storage.blob.aio import ContainerClient, StorageStreamDownloader
from stream_unzip import stream_unzip


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
    for name_bytes, _size, file_chunks in stream_unzip(_drain_queue(chunk_q)):
        name = name_bytes.decode("utf-8")

        if name.endswith("/") or name.startswith("__MACOSX"):
            for _ in file_chunks:
                pass
            continue

        path = f"{extracted_prefix}/{name}"
        data = b"".join(file_chunks)
        asyncio.run_coroutine_threadsafe(file_q.put((path, data)), loop).result()

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
