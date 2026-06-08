"""System healthz router.

This module defines the API endpoints related to system health checks.
"""

from fastapi import APIRouter

from app.healthz.schemas import HealthzResponseModel

healthz_router = APIRouter(prefix="/healthz", tags=["healthz"])


@healthz_router.get("", response_model=HealthzResponseModel)
async def healthz_endpoint() -> HealthzResponseModel:
    """Endpoint to check the health status of the system.

    Returns:
        HealthzResponseModel: A model containing the health
            status of the system.
    """

    return HealthzResponseModel(status="up")
