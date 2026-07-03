"""Pipeline triggers.

This module defines the HTTP triggers for the pipeline blueprint.
"""

from azure.durable_functions import Blueprint
from azure.durable_functions.models import DurableOrchestrationClient
from azure.functions import HttpRequest, HttpResponse
from pydantic import ValidationError

from app.core.language import get_supported_languages
from app.pipeline.schemas import PipelineRequest

pipeline_trigger_bp = Blueprint()


@pipeline_trigger_bp.route(route="pipeline", methods=["POST"])
@pipeline_trigger_bp.durable_client_input(client_name="client")
async def start_analysis(
    req: HttpRequest, client: DurableOrchestrationClient
) -> HttpResponse:
    """Start a new analysis pipeline orchestration.

    Args:
        req(func.HttpRequest): The HTTP request object.
        client(DurableOrchestrationClient): The Durable Functions client.

    Returns:
        func.HttpResponse: A check status response for the new instance.
    """
    try:
        payload = req.get_json()
    except ValueError:
        payload = None

    # Structural validation: reject malformed requests (missing/ill-typed fields).
    try:
        request = PipelineRequest.model_validate(payload)
    except ValidationError as err:
        return HttpResponse(str(err), status_code=400)

    # Domain validation: an empty list means "all supported"; otherwise every
    # requested language must be supported. Well-formed but unsupported -> 422.
    supported = {language.value for language in get_supported_languages()}
    unsupported = sorted(request.languages - supported)
    if unsupported:
        return HttpResponse(
            f"Unsupported language(s): {', '.join(unsupported)}",
            status_code=422,
        )

    instance_id = await client.start_new(
        "analyze", client_input=request.model_dump(mode="json")
    )
    response = client.create_check_status_response(req, instance_id)
    return response  # type: ignore
