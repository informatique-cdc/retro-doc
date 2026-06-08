"""FastAPI entrypoint.

This module contains the entrypoint of the FastAPI app.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.blob_storage import close_blob_storage, init_blob_storage
from app.core.config import settings
from app.core.database import close_database, init_database
from app.core.logger import init_logger

init_logger()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[Any, Any]:
    init_blob_storage()
    await init_database()
    yield
    await close_database()
    await close_blob_storage()


app = FastAPI(
    debug=settings.APP_DEBUG,
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    root_path=settings.APP_ROOT_PATH,
    lifespan=lifespan,
    docs_url="/docs" if settings.APP_DEBUG else None,
    redoc_url="/redoc" if settings.APP_DEBUG else None,
    openapi_url="/openapi.json" if settings.APP_DEBUG else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.APP_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
