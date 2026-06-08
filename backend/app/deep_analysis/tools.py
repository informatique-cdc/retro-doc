"""Deep analysis tools.

This module defines the structured output schema used by the deep analysis agent
to submit its final report via ToolStrategy.
"""

from pydantic import BaseModel


class DeepAnalysisReport(BaseModel):
    """Submit your completed deep analysis report. You MUST call this tool as
    the final step of an analysis to deliver your Markdown report to the user."""

    report: str
