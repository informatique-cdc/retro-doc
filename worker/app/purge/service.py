"""Purge service.

This module defines the service function for purging completed orchestrator instances.
"""

from datetime import datetime

from azure.durable_functions.models import (
    DurableOrchestrationClient,
    OrchestrationRuntimeStatus,
    PurgeHistoryResult,
)


async def purge_history(
    cutoff: datetime, client: DurableOrchestrationClient
) -> PurgeHistoryResult:
    """Purge completed orchestrator instances older than the specified cutoff date.

    Args:
        cutoff(datetime): The cutoff date. Orchestrator instances completed
            before this date will be purged.
        client (DurableOrchestrationClient): The client used to interact with
            orchestration instances.

    Returns:
        PurgeHistoryResult: The result of the purge operation.
    """

    result = await client.purge_instance_history_by(
        created_time_from=datetime(2000, 1, 1),
        created_time_to=cutoff,
        runtime_status=[OrchestrationRuntimeStatus.Completed],
    )
    return result
