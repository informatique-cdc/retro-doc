"""Base class for AST parsing services.

This module defines the abstract base class that all language-specific
AST parsers should inherit from.
"""

from abc import ABC, abstractmethod
from typing import Any

from app.graphs.service.preparer.base import PreparedSource


class ASTParserService(ABC):
    """Base class for parsing services."""

    @abstractmethod
    def parse(
        self,
        source_code: str,
        file_path: str,
        prepared: PreparedSource,
    ) -> dict[str, Any]:
        """Parse source code and return AST data.

        Raises on failure. Returns an empty dict for valid-but-empty input.

        Implementations that derive from `prepared` use it whereas one that
        sources its own representation ignores it.

        Args:
            source_code(str): The source code as a string.
            file_path(str): The file path for metadata/logging purposes.
            prepared(PreparedSource): Whatever this language's
                `SourcePreparerService` returned.

        Returns:
            dict[str, Any]: Dictionary containing AST data.

        Raises:
            PreparedSourceRequiredError: If the implementation derives from
                `prepared` and it is `None` (the preparer failed or had nothing
                to share).
            Exception: If parsing fails.
        """
        ...
