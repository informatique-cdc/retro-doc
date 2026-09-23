"""Base class for source preparation services.

This module defines the abstract base class that all language-specific
source preparers should inherit from.
"""

from abc import ABC, abstractmethod
from typing import Any, TypeAlias

PreparedSource: TypeAlias = Any


class SourcePreparerService(ABC):
    """Base class for source preparation services."""

    @abstractmethod
    def prepare(self, source_code: str, file_path: str) -> PreparedSource:
        """Produce a representation shared by a language's graph services.

        Exists so a language whose AST/CFG/DFG builders all derive from the same
        representation pays for it once instead of once per builder. Languages
        whose builders are genuinely unrelated (a CFG read from bytecode, a DFG
        from an IR) may return `None` and let each builder source its own.

        Args:
            source_code(str): The source code as a string.
            file_path(str): The file path for metadata/logging purposes.

        Returns:
            PreparedSource: The language-private artifact, or `None` when the
                language has nothing worth sharing. Callers run every builder,
                each sourcing its own representation.

        Raises:
            Exception: If the preparation fails. Callers still run every
                builder with `prepared` at `None`, so only the ones that
                derive from it raise `PreparedSourceRequiredError` in turn.
        """
        ...
