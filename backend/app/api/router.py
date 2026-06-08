"""API router.

This module defines the main API router and includes all sub-routers for different endpoints.
Each sub-router is responsible for handling a specific set of related endpoints, such as health
checks, chat functionality, and repository management.
"""

from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_user
from app.chat.router import chat_router
from app.deep_analysis.router import deep_analysis_router
from app.healthz.router import healthz_router
from app.repos.router import repos_router

api_router = APIRouter()

# Public endpoints that do not require authentication
public_routers = [
    healthz_router,
]

# Protected endpoints that require authentication
protected_routers = [
    chat_router,
    deep_analysis_router,
    repos_router,
]

for public_router in public_routers:
    api_router.include_router(public_router)

for protected_router in protected_routers:
    api_router.include_router(
        protected_router, dependencies=[Depends(get_current_user)]
    )
