"""Java AST parsing service.

This module defines the `JavaParsingService` class, which implements the
`ParsingService` interface for parsing Java source code. It uses the `javalang`
library to parse Java code and extract relevant AST structures such as packages,
imports, classes, interfaces, enums, fields, methods, and annotations.
"""

from typing import Any

import javalang

from app.graphs.service.ast_parser.base import ASTParserService


class JavaASTParserService(ASTParserService):
    """Parse Java source code and extract AST structures."""

    def parse(self, source_code: str, file_path: str) -> dict[str, Any]:
        """Parse Java source code and return AST data.

        Args:
            source_code(str): The Java source code as a string.
            file_path(str): The file path for metadata/logging purposes.

        Returns:
            dict[str, Any]: Dictionary containing AST data.

        Raises:
            Exception: If parsing fails.
        """
        tree = javalang.parse.parse(source_code)

        return {
            "file": file_path,
            "package": tree.package.name if tree.package else None,
            "imports": self._extract_imports(tree),
            "classes": self._extract_classes(tree),
            "interfaces": self._extract_interfaces(tree),
            "enums": self._extract_enums(tree),
        }

    def _extract_imports(
        self, tree: javalang.tree.CompilationUnit
    ) -> list[dict[str, Any]]:
        """Extract import statements.

        Args:
            tree(javalang.tree.CompilationUnit): The parsed Java AST.

        Returns:
            list[dict[str, Any]]: A list of dictionaries representing
                import statements.
        """
        return [
            {
                "path": imp.path,
                "static": imp.static,
                "wildcard": imp.wildcard,
            }
            for imp in tree.imports
        ]

    def _extract_classes(
        self, tree: javalang.tree.CompilationUnit
    ) -> list[dict[str, Any]]:
        """Extract class declarations.

        Args:
            tree(javalang.tree.CompilationUnit): The parsed Java AST.

        Returns:
            list[dict[str, Any]]: A list of dictionaries representing
                class declarations.
        """
        classes = []
        for _path, node in tree.filter(javalang.tree.ClassDeclaration):
            classes.append(
                {
                    "name": node.name,
                    "type": "class",
                    "modifiers": list(node.modifiers) if node.modifiers else [],
                    "extends": node.extends.name if node.extends else None,
                    "implements": (
                        [impl.name for impl in node.implements]
                        if node.implements
                        else []
                    ),
                    "annotations": self._extract_annotations(node),
                    "fields": self._extract_fields(node),
                    "methods": self._extract_methods(node),
                    "line_start": (node.position.line if node.position else None),
                }
            )
        return classes

    def _extract_interfaces(
        self, tree: javalang.tree.CompilationUnit
    ) -> list[dict[str, Any]]:
        """Extract interface declarations.

        Args:
            tree(javalang.tree.CompilationUnit): The parsed Java AST.

        Returns:
            list[dict[str, Any]]: A list of dictionaries representing
                interface declarations.
        """
        interfaces = []
        for _path, node in tree.filter(javalang.tree.InterfaceDeclaration):
            interfaces.append(
                {
                    "name": node.name,
                    "type": "interface",
                    "modifiers": list(node.modifiers) if node.modifiers else [],
                    "extends": (
                        [ext.name for ext in node.extends] if node.extends else []
                    ),
                    "annotations": self._extract_annotations(node),
                    "methods": self._extract_methods(node),
                    "line_start": (node.position.line if node.position else None),
                }
            )
        return interfaces

    def _extract_enums(
        self, tree: javalang.tree.CompilationUnit
    ) -> list[dict[str, Any]]:
        """Extract enum declarations.

        Args:
            tree(javalang.tree.CompilationUnit): The parsed Java AST.

        Returns:
            list[dict[str, Any]]: A list of dictionaries representing
                enum declarations.
        """
        enums = []
        for _path, node in tree.filter(javalang.tree.EnumDeclaration):
            enums.append(
                {
                    "name": node.name,
                    "type": "enum",
                    "modifiers": list(node.modifiers) if node.modifiers else [],
                    "implements": (
                        [impl.name for impl in node.implements]
                        if node.implements
                        else []
                    ),
                    "body": (
                        [const.name for const in node.body.constants]
                        if node.body and node.body.constants
                        else []
                    ),
                    "line_start": (node.position.line if node.position else None),
                }
            )
        return enums

    def _extract_annotations(self, node: Any) -> list[dict[str, Any]]:
        """Extract annotations from a node.

        Args:
            node(Any): A javalang AST node that may have annotations.

        Returns:
            list[dict[str, Any]]: A list of dictionaries representing
                annotations.
        """
        if not hasattr(node, "annotations") or not node.annotations:
            return []

        annotations = []
        for ann in node.annotations:
            element: list[str] | str | None = None
            if hasattr(ann, "element") and ann.element:
                if isinstance(ann.element, list):
                    element = [str(e) for e in ann.element]
                else:
                    element = str(ann.element)

            annotations.append({"name": ann.name, "element": element})
        return annotations

    def _extract_fields(self, class_node: Any) -> list[dict[str, Any]]:
        """Extract field declarations from a class.

        Args:
            class_node(Any): A javalang AST node representing a class.

        Returns:
            list[dict[str, Any]]: A list of dictionaries representing
                field declarations.
        """
        fields = []
        for field in class_node.fields:
            for declarator in field.declarators:
                fields.append(
                    {
                        "name": declarator.name,
                        "type": self._get_type_name(field.type),
                        "modifiers": (list(field.modifiers) if field.modifiers else []),
                        "annotations": self._extract_annotations(field),
                        "line_start": (field.position.line if field.position else None),
                    }
                )
        return fields

    def _extract_methods(self, class_node: Any) -> list[dict[str, Any]]:
        """Extract method declarations from a class or interface.

        Args:
            class_node(Any): A javalang AST node representing a class
                or interface.

        Returns:
            list[dict[str, Any]]: A list of dictionaries representing
                method declarations.
        """
        methods = []
        for method in class_node.methods:
            throws = []
            if method.throws:
                for exc in method.throws:
                    if isinstance(exc, str):
                        throws.append(exc)
                    elif hasattr(exc, "name"):
                        throws.append(exc.name)
                    else:
                        throws.append(str(exc))

            methods.append(
                {
                    "name": method.name,
                    "return_type": (
                        self._get_type_name(method.return_type)
                        if method.return_type
                        else "void"
                    ),
                    "modifiers": (list(method.modifiers) if method.modifiers else []),
                    "parameters": self._extract_parameters(method),
                    "annotations": self._extract_annotations(method),
                    "throws": throws,
                    "line_start": (method.position.line if method.position else None),
                }
            )
        return methods

    def _extract_parameters(self, method: Any) -> list[dict[str, Any]]:
        """Extract method parameters.

        Args:
            method(Any): A javalang AST node representing a method.

        Returns:
            list[dict[str, Any]]: A list of dictionaries representing
                method parameters.
        """
        if not method.parameters:
            return []

        return [
            {
                "name": param.name,
                "type": self._get_type_name(param.type),
                "varargs": getattr(param, "varargs", False),
            }
            for param in method.parameters
        ]

    def _get_type_name(self, type_node: Any) -> str:
        """Get string representation of a type, including generics
        and arrays.

        Args:
            type_node(Any): A javalang AST node representing a type.

        Returns:
            str: A string representation of the type.
        """
        if type_node is None:
            return "unknown"

        if isinstance(type_node, str):
            return type_node

        if not hasattr(type_node, "name"):
            return str(type_node)

        type_name = (
            type_node.name if isinstance(type_node.name, str) else str(type_node.name)
        )

        # Handle generic type arguments
        if hasattr(type_node, "arguments") and type_node.arguments:
            args = []
            for arg in type_node.arguments:
                if hasattr(arg, "type"):
                    args.append(self._get_type_name(arg.type))
                elif isinstance(arg, str):
                    args.append(arg)
                else:
                    args.append(str(arg))
            type_name += f"<{', '.join(args)}>"

        # Handle array dimensions
        if hasattr(type_node, "dimensions") and type_node.dimensions:
            type_name += "[]" * len(type_node.dimensions)

        return type_name
