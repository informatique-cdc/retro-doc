"""Base class for symbol table building services.

This module defines the abstract base class that all language-specific
symbol table builders should inherit from.
"""

from abc import ABC, abstractmethod
from typing import Any


class SymbolTableBuilderService(ABC):
    """Base class for symbol table building services."""

    @abstractmethod
    def build(self, source_code: str, file_path: str) -> dict[str, Any] | None:
        """Build a symbol table for a single file from its AST data.

        To skip the step, return an empty dict.

        Args:
            source_code(str): The raw source code of the file.
            file_path(str): The file path for metadata/logging purposes.

        Returns:
            dict[str, Any] | None: Dict containing the symbol table with
                symbols and scopes. None if building fails.
        """
        ...
