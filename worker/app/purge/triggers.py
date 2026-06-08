"""Purge triggers.

This module defines the purge triggers for the purge blueprint.
"""

from datetime import datetime, timedelta

from azure.durable_functions import Blueprint
from azure.durable_functions.models import (
    DurableOrchestrationClient,
)
from azure.functions import TimerRequest
from loguru import logger

from app.core.config import settings
from app.purge.service import purge_history as purge_history_service

purge_trigger_bp = Blueprint()


@purge_trigger_bp.timer_trigger(schedule="0 0 0 * * *", arg_name="timer")
@purge_trigger_bp.durable_client_input(client_name="client")
async def purge_history(
    timer: TimerRequest, client: DurableOrchestrationClient
) -> None:
    """Purge completed orchestrator instances older than 30 days.

    Args:
        timer(TimerRequest): The timer trigger input (not used in the function).
        client(DurableOrchestrationClient): The client used to interact with
            orchestration instances.
    """
    cutoff = datetime.now(settings.APP_TIMEZONE) - timedelta(days=30)
    result = await purge_history_service(cutoff, client)
    logger.info(
        f"Purged {result.instances_deleted} completed orchestrator instances older than 30 days."
    )
