"""Database initialization.

This module initializes the MongoDB client and sets up the Beanie ODM with the
defined document models. After initialization, the client and database are
accessible via get_client() and get_database().
"""

from collections.abc import Sequence

from beanie import Document, init_beanie
from loguru import logger
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.chat.models import ChatMessageDocument, ChatThreadDocument
from app.core.config import settings
from app.deep_analysis.models import DeepAnalysisDocument
from app.docs.models import FileDocumentationDocument, RepoMetaDocument
from app.graphs.models import ASTDocument, CFGDocument, DFGDocument
from app.pipeline.models import PipelineRunDocument
from app.repos.models import FileDocument, RepoDocument
from app.users.models import UserRepoDocument

_client: AsyncMongoClient[dict[str, object]] | None = None
_database: AsyncDatabase[dict[str, object]] | None = None


async def init_database() -> None:
    """Initialize MongoDB client and Beanie ODM.

    Creates the AsyncMongoClient, selects the database, and initializes
    Beanie with the registered document models.
    """
    global _client, _database

    logger.info("Core: Initializing MongoDB resources...")

    _client = AsyncMongoClient(settings.MONGODB_CONNECTION_STR.get_secret_value())
    _database = _client[settings.MONGODB_DB_NAME]

    doc_models: Sequence[type[Document]] = [
        UserRepoDocument,
        RepoDocument,
        FileDocument,
        RepoMetaDocument,
        PipelineRunDocument,
        FileDocumentationDocument,
        ASTDocument,
        CFGDocument,
        DFGDocument,
        ChatThreadDocument,
        ChatMessageDocument,
        DeepAnalysisDocument,
    ]

    await init_beanie(database=_database, document_models=doc_models)

    logger.info("Core: MongoDB resources initialized.")


async def close_database() -> None:
    """Close the MongoDB client connection."""
    global _client, _database

    logger.info("Core: Closing MongoDB resources...")

    client = get_client()
    await client.close()

    _client = None
    _database = None

    logger.info("Core: MongoDB resources closed.")


def get_client() -> AsyncMongoClient[dict[str, object]]:
    """Return the initialized MongoDB client.

    Raises:
        RuntimeError: If `init_database()` has not been called yet.
    """
    global _client

    if _client is None:
        raise RuntimeError("Database not initialized. Call init_database() first")
    return _client


def get_database() -> AsyncDatabase[dict[str, object]]:
    """Return the initialized MongoDB database.

    Raises:
        RuntimeError: If `init_database()` has not been called yet.
    """
    global _database

    if _database is None:
        raise RuntimeError("Database not initialized. Call init_database() first")
    return _database
