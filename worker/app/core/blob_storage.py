"""Azure Blob Storage initialization.

This module initializes the Azure Blob Storage client and container client.
After initialization, they are accessible via get_blob_service() and
get_container_client().

Since there is no lifespan management for Azure Durable Functions, the clients here
are initialized once but never closed. This is acceptable since they are designed
to be long-lived and the Azure Functions runtime will handle cleanup when the
function instance is recycled.
"""

from azure.identity import InteractiveBrowserCredential
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient, ContainerClient
from loguru import logger

from app.core.config import settings

_blob_service: BlobServiceClient | None = None
_container_client: ContainerClient | None = None


def init_blob_storage() -> None:
    """Initialize the Azure Blob Storage client.

    Selects the credential based on APP_DEBUG:
    - True: InteractiveBrowserCredential (interactive login)
    - False: DefaultAzureCredential (managed identity / env vars)
    """
    logger.info("Core: Initializing Blob Storage resources...")
    global _blob_service, _container_client

    credential = (
        InteractiveBrowserCredential()
        if settings.APP_DEBUG
        else DefaultAzureCredential()
    )

    _blob_service = BlobServiceClient(
        settings.BLOB_STORAGE_ACCOUNT_URL,
        credential=credential,  # type: ignore[arg-type]
    )
    _container_client = _blob_service.get_container_client(
        settings.BLOB_STORAGE_CONTAINER_NAME
    )
    logger.info("Core: Blob Storage resources initialized.")


def get_blob_service() -> BlobServiceClient:
    """Return the initialized Blob Storage service client.

    Raises:
        RuntimeError: If init_blob_storage() has not been called yet.
    """
    if _blob_service is None:
        raise RuntimeError(
            "Blob storage not initialized. Call init_blob_storage() first."
        )
    return _blob_service


def get_container_client() -> ContainerClient:
    """Return the initialized container client.

    Raises:
        RuntimeError: If init_blob_storage() has not been called yet.
    """
    if _container_client is None:
        raise RuntimeError(
            "Blob storage not initialized. Call init_blob_storage() first."
        )
    return _container_client
