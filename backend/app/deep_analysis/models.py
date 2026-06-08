"""Deep analysis Object Document Models (ODM).

This module defines the ODM related to deep analysis.
"""

from datetime import UTC, datetime
from enum import Enum

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.deep_analysis.config import deep_analysis_settings


class DeepAnalysisStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DeepAnalysisDocument(Document):
    user_id: str
    repo_id: PydanticObjectId
    query: str
    status: DeepAnalysisStatus = DeepAnalysisStatus.PENDING
    content: str | None = None
    error: str | None = None
    progress_current: int = 0
    progress_total: int = Field(
        default_factory=lambda: (
            deep_analysis_settings.DEEP_AGENT_MODEL_CALL_LIMIT
            + deep_analysis_settings.DEEP_AGENT_TASK_CALL_LIMIT
            * deep_analysis_settings.DEEP_AGENT_SUBAGENT_MODEL_CALL_LIMIT
        )
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_heartbeat_at: datetime | None = None
    finished_at: datetime | None = None

    class Settings:
        name = "deep_analyses"
        keep_nulls = False
        indexes = [
            IndexModel([("user_id", 1), ("repo_id", 1), ("created_at", -1)]),
        ]
