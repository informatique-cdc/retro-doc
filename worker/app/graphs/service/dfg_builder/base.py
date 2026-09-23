"""Base class for DFG building services.

This module defines the abstract base class that all language-specific
DFG builders should inherit from.
"""

from abc import ABC, abstractmethod
from typing import Any

from app.graphs.service.preparer.base import PreparedSource


class DFGBuilderService(ABC):
    """Base class for DFG building services."""

    @abstractmethod
    def build(
        self,
        source_code: str,
        file_path: str,
        prepared: PreparedSource,
    ) -> list[dict[str, Any]]:
        """Build data flow graphs for all methods in a file.

        Raises on failure. Returns an empty list for valid-but-empty input.

        Implementations that derive from `prepared` use it whereas one that
        sources its own representation ignores it.

        Args:
            source_code(str): The raw source code of the file.
            file_path(str): The file path for metadata/logging purposes.
            prepared(PreparedSource): Whatever this language's
                `SourcePreparerService` returned.

        Returns:
            list[dict[str, Any]]: List of dicts, one per method,
            each containing the DFG representation with nodes, edges,
            defs and uses.

        Raises:
            PreparedSourceRequiredError: If the implementation derives from
                `prepared` and it is `None` (the preparer failed or had nothing
                to share).
            Exception: If DFG building fails.
        """
        ...
