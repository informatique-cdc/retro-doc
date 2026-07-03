"""Languages triggers.

This module defines the HTTP triggers for the languages blueprint.
"""

from azure.durable_functions import Blueprint
from azure.functions import HttpRequest, HttpResponse

from app.core.language import get_supported_languages
from app.languages.schemas import LanguagesResponse

languages_trigger_bp = Blueprint()


@languages_trigger_bp.route(route="languages")
def list_languages(req: HttpRequest) -> HttpResponse:
    """List the programming languages supported by the analysis pipeline.

    Args:
        req(HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: A JSON object with the list of supported languages.
    """
    response = LanguagesResponse(languages=get_supported_languages())
    return HttpResponse(
        response.model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
