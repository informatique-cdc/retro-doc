"""System healthz router.

This module defines the API endpoints related to system health checks.
"""

from fastapi import APIRouter

from app.healthz.schemas import HealthzResponse

healthz_router = APIRouter(prefix="/healthz", tags=["healthz"])


@healthz_router.get("", response_model=HealthzResponse)
async def healthz_endpoint() -> HealthzResponse:
    """Endpoint to check the health status of the system.

    Returns:
        HealthzResponse: A model containing the health
            status of the system.
    """

    return HealthzResponse(status="up")
