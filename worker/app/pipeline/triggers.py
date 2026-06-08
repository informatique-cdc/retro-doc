"""Pipeline triggers.

This module defines the HTTP triggers for the pipeline blueprint.
"""

from azure.durable_functions import Blueprint
from azure.durable_functions.models import DurableOrchestrationClient
from azure.functions import HttpRequest, HttpResponse

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
    instance_id = await client.start_new("analyze", client_input=payload)
    response = client.create_check_status_response(req, instance_id)
    return response  # type: ignore
