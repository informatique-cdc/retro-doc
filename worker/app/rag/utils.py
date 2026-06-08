"""RAG utilities.

This module provides helper functions for the RAG indexing pipeline.
"""

import hashlib


def make_chunk_id(repo_id: str, file_path: str, chunk_index: int) -> str:
    """Produce a deterministic, collision-resistant document ID.

    Azure AI Search uses this as the document key. Re-uploading with the
    same ID overwrites the existing document (upsert), making indexing
    idempotent.

    Args:
        repo_id (str): The repository identifier.
        file_path (str): The relative file path within the repository.
        chunk_index (int): The zero-based index of the chunk.

    Returns:
        str: A hex-encoded SHA-256 hash suitable as an Azure AI Search
            document key.
    """
    raw = f"{repo_id}:{file_path}:{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()
