"""Base class for CFG building services.

This module defines the abstract base class that all language-specific
CFG builders should inherit from.
"""

from abc import ABC, abstractmethod
from typing import Any


class CFGBuilderService(ABC):
    """Base class for CFG building services."""

    @abstractmethod
    def build(self, source_code: str, file_path: str) -> list[dict[str, Any]]:
        """Build control flow graphs for all methods in a file.

        Raises on failure. Returns an empty list for valid-but-empty input.

        Args:
            source_code(str): The raw source code of the file.
            file_path(str): The file path for metadata/logging purposes.

        Returns:
            list[dict[str, Any]]: List of dicts, one per method,
                each containing the CFG representation with nodes,
                edges, and metrics.

        Raises:
            Exception: If CFG building fails.
        """
        pass
