"""Logger configuration.

This module initializes the Loguru logger and overrides the default Python warning system
to route warnings through Loguru. Logs are forwarded to Python's standard logging module
so the Azure Functions worker can send them to the host.
"""

from __future__ import annotations

import logging
import warnings
from typing import TYPE_CHECKING, Any

from loguru import logger

from app.core.config import settings

if TYPE_CHECKING:
    import loguru


def _loguru_warning_handler(
    message: Warning | str, category: type[Warning], filename: str, lineno: int, *_: Any
) -> None:
    """Custom warning handler that routes default warning system through Loguru.

    Args:
        message(Warning | str): The warning message.
        category(type[Warning]): The category of the warning.
        filename(str): The name of the file where the warning occurred.
        lineno(int): The line number in the file where the warning occurred.
        *_: Any additional arguments (ignored).
    """
    logger.opt(depth=2).warning(
        f"{category.__name__}: {message} (in {filename}:{lineno})"
    )


def _standard_logging_sink(message: loguru.Message) -> None:
    """Forward Loguru records to Python standard logging.

    The Azure Functions Python worker intercepts standard logging and forwards
    it to the host via gRPC, where it is displayed according to host.json logLevel.

    Args:
        message(loguru.Message): The Loguru message to forward.
    """
    record = message.record
    logging.getLogger(record["name"]).log(record["level"].no, message.rstrip("\n"))


def init_logger() -> None:
    """Initialize the Loguru logger with the specified configuration.

    This function configures the Loguru logger to forward logs to Python's standard
    logging module (for Azure Functions host integration) and optionally to a file.
    It also silences verbose Azure SDK loggers by raising their level to WARNING,
    and overrides the default Python warning system to route warnings through Loguru.
    """
    # Set up Loguru logger
    logger.remove()

    # Forward to standard logging so Azure Functions host picks it up
    logger.add(
        _standard_logging_sink,
        level=settings.LOG_LEVEL,
        diagnose=settings.LOG_DIAGNOSE,
    )

    # In debug mode, log to a file
    if settings.APP_DEBUG:
        logger.add(
            "logs/app.log",
            level=settings.LOG_LEVEL,
            diagnose=settings.LOG_DIAGNOSE,
            rotation="100 MB",
            retention="7 days",
        )

    # Silence verbose Azure SDK loggers (they use standard Python logging)
    for namespace in ("azure", "azure.core", "azure.identity", "msal"):
        logging.getLogger(namespace).setLevel(logging.WARNING)

    # Override the default warning system to route warnings through Loguru
    warnings.showwarning = _loguru_warning_handler  # type: ignore
