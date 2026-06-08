"""Azure Durable Functions entrypoint.

This file is the entrypoint for the Azure Functions runtime. It can't
be renamed or moved. All function definitions live in the `app/` package.
"""

from azure.durable_functions import DFApp

from app.api.router import register_blueprints
from app.core.blob_storage import init_blob_storage
from app.core.config import settings
from app.core.database import init_database
from app.core.logger import init_logger
from app.rag.vectorstore import init_vectorstore

init_logger()

# Initialize clients
init_blob_storage()
init_database()
init_vectorstore()

app = DFApp(http_auth_level=settings.APP_AUTH_LEVEL)

# Register blueprints (functions)
register_blueprints(app)
