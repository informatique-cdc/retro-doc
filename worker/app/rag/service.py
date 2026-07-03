"""RAG service.

This module defines the service layer for the RAG-related operations.
"""

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from loguru import logger

from app.rag.config import rag_settings
from app.rag.utils import make_chunk_id
from app.rag.vectorstore import azure_ai_search_retry, get_vectorstore

_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
    model_name=rag_settings.EMBEDDING_MODEL_NAME,
    chunk_size=rag_settings.CHUNK_SIZE,
    chunk_overlap=rag_settings.CHUNK_OVERLAP,
)


async def index_file_documentation(
    repo_id: str,
    file_id: str,
    file_path: str,
    content: str,
) -> bool:
    """Chunk and index file documentation into Azure AI Search.

    Splits the documentation content into token-aware chunks, assigns
    deterministic IDs for idempotent upserts, and writes them to the
    vectorstore. The write is all-or-nothing: the underlying batch upload
    raises on any failure, so this either indexes every chunk or none.

    Args:
        repo_id(str): The repository identifier.
        file_id(str): The file identifier.
        file_path(str): The relative file path within the repository.
        content(str): The documentation content to index.

    Returns:
        bool: `True` if the documentation was indexed, `False` on failure.
    """
    try:
        vector_store = get_vectorstore()

        raw_doc = Document(
            page_content=content,
            metadata={
                "repo_id": repo_id,
                "file_id": file_id,
                "file_path": file_path,
            },
        )

        file_chunks = _splitter.split_documents([raw_doc])

        for i, chunk in enumerate(file_chunks):
            chunk.page_content = (
                f"Documentation of the file: {file_path}\n\n" + chunk.page_content
            )
            chunk.id = make_chunk_id(repo_id, file_path, i)
            chunk.metadata["chunk_index"] = i

        await azure_ai_search_retry(vector_store.aadd_documents, documents=file_chunks)

        return True
    except Exception:
        logger.exception(f"RAG: Indexing failed for '{file_path}'")
        return False
