"""Version service.

This module defines the service layer for the version-related operations.
"""

# Analyzer version. Bump when analysis output changes
ANALYZER_VERSION = "0.1.0"


def get_analyzer_version() -> str:
    """Get the version of the analyzer this worker runs.

    Returns:
        str: The analyzer version.
    """
    return ANALYZER_VERSION
