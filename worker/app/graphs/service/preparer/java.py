"""Java source preparation service.

This module defines the `JavaSourcePreparerService` class, which parses Java
source code once so the AST parser, CFG builder and DFG builder can all work
from the same `javalang` tree.
"""

import javalang

from app.graphs.service.preparer.base import PreparedSource, SourcePreparerService


class JavaSourcePreparerService(SourcePreparerService):
    """Parse Java source code once for all Java graph services."""

    def prepare(self, source_code: str, file_path: str) -> PreparedSource:
        """Parse Java source code into a `javalang` compilation unit.

        Sharing one tree also makes the positional alignment between the AST
        parser's canonical keys and the CFG/DFG traversals hold by construction,
        rather than relying on separate parses iterating in the same order.

        Args:
            source_code(str): The Java source code as a string.
            file_path(str): The file path for metadata/logging purposes.

        Returns:
            PreparedSource: The parsed `javalang` compilation unit.

        Raises:
            Exception: If parsing fails.
        """
        return javalang.parse.parse(source_code)
