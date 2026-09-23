"""Version triggers.

This module defines the HTTP triggers for the version blueprint.
"""

from azure.durable_functions import Blueprint
from azure.functions import HttpRequest, HttpResponse

from app.version.schemas import VersionResponse
from app.version.service import get_analyzer_version

version_trigger_bp = Blueprint()


@version_trigger_bp.route(route="version")
def get_version(req: HttpRequest) -> HttpResponse:
    """Get the version of the analyzer this worker runs.

    Args:
        req(HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: A JSON object with the analyzer version.
    """
    response = VersionResponse(version=get_analyzer_version())
    return HttpResponse(
        response.model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
