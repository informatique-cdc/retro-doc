"""Base class for AST parsing services.

This module defines the abstract base class that all language-specific
AST parsers should inherit from.
"""

from abc import ABC, abstractmethod
from typing import Any


class ASTParserService(ABC):
    """Base class for parsing services."""

    @abstractmethod
    def parse(self, source_code: str, file_path: str) -> dict[str, Any]:
        """Parse source code and return AST data.

        Raises on failure. Returns an empty dict for valid-but-empty input.

        Args:
            source_code(str): The source code as a string.
            file_path(str): The file path for metadata/logging purposes.

        Returns:
            dict[str, Any]: Dictionary containing AST data.

        Raises:
            Exception: If parsing fails.
        """
        pass
