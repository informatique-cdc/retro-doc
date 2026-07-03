"""API router.

This module defines the API router for the application. It imports and
registers all the blueprints for the application.
"""

from azure.durable_functions import DFApp

from app.healthz.triggers import healthz_trigger_bp
from app.languages.triggers import languages_trigger_bp
from app.pipeline.activities import pipeline_activity_bp
from app.pipeline.orchestrators import pipeline_orch_bp
from app.pipeline.triggers import pipeline_trigger_bp
from app.purge.triggers import purge_trigger_bp

# Blueprints to register
blueprints = [
    healthz_trigger_bp,
    languages_trigger_bp,
    pipeline_activity_bp,
    pipeline_orch_bp,
    pipeline_trigger_bp,
    purge_trigger_bp,
]


def register_blueprints(app: DFApp) -> None:
    """Register blueprints with the Durable Functions app.

    Args:
        app(DFApp): The Durable Functions app instance to
            register the blueprints with.
    """
    for blueprint in blueprints:
        app.register_functions(blueprint)
