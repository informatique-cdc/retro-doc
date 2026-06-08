"""Healthz triggers.

This module defines the healthz trigger for the healthz blueprint.
"""

from azure.durable_functions import Blueprint
from azure.functions import HttpRequest, HttpResponse

from app.healthz.schemas import HealthzResponseModel

healthz_trigger_bp = Blueprint()


@healthz_trigger_bp.route(route="healthz")
def healthz(req: HttpRequest) -> HttpResponse:
    """Health check endpoint that returns the status of the application.

    Args:
        req(HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: A JSON response containing the status of the application.
    """
    return HttpResponse(
        HealthzResponseModel(status="up").model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
