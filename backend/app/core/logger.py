"""Logger configuration.

This module initializes the Loguru logger and overrides the default Python warning system
to route warnings through Loguru.
"""

import sys
import warnings
from typing import Any

from loguru import logger

from app.core.config import settings


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


def init_logger() -> None:
    """Initialize the Loguru logger with the specified configuration.

    This function configures the Loguru logger to output logs to standard error with
    custom settings. It also overrides the default Python warning system
    to route warnings through Loguru.
    """
    # Set up Loguru logger
    logger.remove()
    logger.add(
        sys.stderr,
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

    # Override the default warning system to route warnings through Loguru
    warnings.showwarning = _loguru_warning_handler  # type: ignore
