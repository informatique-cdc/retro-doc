"""Healthz Pydantic schemas.

This modules defines the schemas for the healthz check endpoint (e.g., Data Transfer Object - DTO).
"""

from typing import Literal

from pydantic import BaseModel


class HealthzResponseModel(BaseModel):
    status: Literal["up", "down"]
