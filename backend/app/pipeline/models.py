"""Pipeline Object Document Models (ODM).

This module defines the ODM related to Pipeline Runs.
"""

from datetime import UTC, datetime
from enum import Enum

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import IndexModel


class PipelineMeta(BaseModel):
    message: str
    step: str


class PipelineStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class PipelineRunDocument(Document):
    """One attempt at analyzing a repository.

    A repository accumulates one document per attempt, ordered by `_id`: a
    retry inserts a new run rather than reviving the failed one, so the attempt
    that failed keeps its `status` and `meta` for whoever wants to read why, and
    `retried_at` marks it superseded.

    RUNNING belongs in the index filter alongside PENDING: the claim it enforces
    (`_claim_next_run`) has to hold for as long as the work does, not just until
    the worker picks the run up.

    That pair is `$gte "pending"` because Cosmos DB (RU) rejects `$in` in a
    `partialFilterExpression`. It works because sorted, the values read
    `completed < failed < pending < running`.
    """

    repo_id: PydanticObjectId
    status: PipelineStatus = PipelineStatus.PENDING
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    retried_at: datetime | None = None
    meta: PipelineMeta | None = None

    class Settings:
        name = "pipeline_runs"
        keep_nulls = False
        indexes = [
            IndexModel(
                [("repo_id", 1)],
                partialFilterExpression={
                    "status": {"$gte": PipelineStatus.PENDING.value}
                },
                unique=True,
            ),
        ]
