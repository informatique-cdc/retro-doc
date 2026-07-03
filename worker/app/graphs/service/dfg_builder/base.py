"""Base class for DFG building services.

This module defines the abstract base class that all language-specific
DFG builders should inherit from.
"""

from abc import ABC, abstractmethod
from typing import Any


class DFGBuilderService(ABC):
    """Base class for DFG building services."""

    @abstractmethod
    def build(self, source_code: str, file_path: str) -> list[dict[str, Any]]:
        """Build data flow graphs for all methods in a file.

        Raises on failure. Returns an empty list for valid-but-empty input.

        Args:
            source_code(str): The raw source code of the file.
            file_path(str): The file path for metadata/logging purposes.

        Returns:
            list[dict[str, Any]]: List of dicts, one per method,
            each containing the DFG representation with nodes, edges,
            defs and uses.

        Raises:
            Exception: If DFG building fails.
        """
        ...
