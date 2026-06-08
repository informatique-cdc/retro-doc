"""Pipeline Object Document Models (ODM).

This module defines the ODM related to Pipeline Runs.
"""

from datetime import datetime
from enum import Enum

from beanie import Document, PydanticObjectId
from pydantic import Field

from app.core.config import settings


class PipelineStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class PipelineRunDocument(Document):
    repo_id: PydanticObjectId
    status: PipelineStatus = PipelineStatus.PENDING
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(settings.APP_TIMEZONE)
    )
    finished_at: datetime | None = None
    meta: str | None = None

    class Settings:
        name = "pipeline_runs"
