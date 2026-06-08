"""Java symbol table building service.

This module builds a per-file symbol table from Java source code,
extracting classes, interfaces, enums, methods, fields, and parameters.
"""

from functools import partial
from typing import Any

import javalang
from javalang.tree import (
    ClassDeclaration,
    EnumDeclaration,
    InterfaceDeclaration,
)
from loguru import logger

from app.graphs.service.symbol_table_builder.base import (
    SymbolTableBuilderService,
)


class JavaSymbolTableBuilderService(SymbolTableBuilderService):
    """Build a symbol table for a single Java file from its source code."""

    def build(self, source_code: str, file_path: str) -> dict[str, Any] | None:
        """Build a symbol table from Java source code.

        Args:
            source_code(str): The raw Java source code.
            file_path(str): The file path for metadata/logging purposes.

        Returns:
            dict[str, Any] | None: Dict containing the symbol table with
                symbols and scopes. None if building fails.
        """
        try:
            tree = javalang.parse.parse(source_code)
        except Exception as e:
            logger.exception(
                f"JavaSymbolTableBuilderService: Error parsing {file_path}"
            )
            return None

        symbols: dict[str, dict[str, Any]] = {}
        scopes: dict[str, list[str]] = {}
        counter = [0]

        package = tree.package.name if tree.package else ""

        add_symbol = partial(self._add_symbol, symbols, scopes, counter, file_path)

        for _, node in tree.filter(ClassDeclaration):
            self._process_class(node, package, add_symbol)

        for _, node in tree.filter(InterfaceDeclaration):
            self._process_interface(node, package, add_symbol)

        for _, node in tree.filter(EnumDeclaration):
            self._process_enum(node, package, add_symbol)

        return {"symbols": symbols, "scopes": scopes}

    def _add_symbol(
        self,
        symbols: dict[str, dict[str, Any]],
        scopes: dict[str, list[str]],
        counter: list[int],
        file_path: str,
        *,
        name: str,
        symbol_type: str,
        scope: str,
        data_type: str | None = None,
        line: int | None = None,
        modifiers: list[str] | None = None,
        annotations: list[dict[str, Any]] | None = None,
    ) -> str:
        """Add a symbol to the symbol table and return its unique ID.

        Args:
            symbols(dict): The symbol table dict to add to.
            scopes(dict): The scopes dict to update.
            counter(list): A single-item list acting as a mutable counter for unique IDs.
            file_path(str): The file path for symbol metadata.
            name(str): The symbol name.
            symbol_type(str): The type of symbol (e.g. class, method).
            scope(str): The scope the symbol belongs to.
            data_type(str, optional): The data type for variables/methods.
            line(int, optional): The line number where the symbol is defined.
            modifiers(list[str], optional): List of symbol modifiers (e.g. public, static).
            annotations(list[dict], optional): List of symbol annotations.

        Returns:
            str: The unique ID of the added symbol.
        """
        counter[0] += 1
        sid = f"{symbol_type}_{scope}_{name}_{counter[0]}"
        symbols[sid] = {
            "id": sid,
            "name": name,
            "type": symbol_type,
            "data_type": data_type,
            "scope": scope,
            "file": file_path,
            "line": line,
            "modifiers": modifiers or [],
            "annotations": annotations or [],
        }
        scopes.setdefault(scope, []).append(sid)
        return sid

    # ------------------------------------------------------------------
    # Processing helpers
    # ------------------------------------------------------------------

    def _extract_annotations(self, node: Any) -> list[dict[str, Any]]:
        """Extract annotations from a javalang AST node.

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

    def _get_type_name(self, type_node: Any) -> str:
        """Get string representation of a type, including generics and arrays.

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

        if hasattr(type_node, "dimensions") and type_node.dimensions:
            type_name += "[]" * len(type_node.dimensions)

        return type_name

    def _process_class(
        self,
        node: ClassDeclaration,
        package: str,
        add_symbol: Any,
    ) -> None:
        """Process a class declaration and its members, adding symbols to the table.

        Args:
            node(ClassDeclaration): The javalang class declaration node.
            package(str): The package name for scoping.
            add_symbol(Callable): The function to call to add symbols to the table.
        """
        class_name = node.name
        scope = f"{package}.{class_name}" if package else class_name

        add_symbol(
            name=class_name,
            symbol_type="class",
            scope=package or "default",
            line=node.position.line if node.position else None,
            modifiers=list(node.modifiers) if node.modifiers else [],
            annotations=self._extract_annotations(node),
        )

        for field in node.fields:
            for declarator in field.declarators:
                add_symbol(
                    name=declarator.name,
                    symbol_type="field",
                    data_type=self._get_type_name(field.type),
                    scope=scope,
                    line=field.position.line if field.position else None,
                    modifiers=list(field.modifiers) if field.modifiers else [],
                    annotations=self._extract_annotations(field),
                )

        for method in node.methods:
            self._process_method(method, scope, add_symbol)

    def _process_interface(
        self,
        node: InterfaceDeclaration,
        package: str,
        add_symbol: Any,
    ) -> None:
        """Process an interface declaration and its members, adding symbols to the table.

        Args:
            node(InterfaceDeclaration): The javalang interface declaration node.
            package(str): The package name for scoping.
            add_symbol(Callable): The function to call to add symbols to the table.
        """
        intf_name = node.name
        scope = f"{package}.{intf_name}" if package else intf_name

        add_symbol(
            name=intf_name,
            symbol_type="interface",
            scope=package or "default",
            line=node.position.line if node.position else None,
            modifiers=list(node.modifiers) if node.modifiers else [],
            annotations=self._extract_annotations(node),
        )

        for method in node.methods:
            self._process_method(method, scope, add_symbol)

    def _process_enum(
        self,
        node: EnumDeclaration,
        package: str,
        add_symbol: Any,
    ) -> None:
        """Process an enum declaration, adding a symbol to the table.

        Args:
            node(EnumDeclaration): The javalang enum declaration node.
            package(str): The package name for scoping.
            add_symbol(Callable): The function to call to add symbols to the table.
        """
        add_symbol(
            name=node.name,
            symbol_type="enum",
            scope=package or "default",
            line=node.position.line if node.position else None,
            modifiers=list(node.modifiers) if node.modifiers else [],
        )

    def _process_method(
        self,
        method: Any,
        class_scope: str,
        add_symbol: Any,
    ) -> None:
        """Process a method declaration and its parameters, adding symbols to the table.

        Args:
            method(Any): The javalang MethodDeclaration node.
            class_scope(str): The scope of the containing class/interface.
            add_symbol(Callable): The function to call to add symbols to the table.
        """
        method_name = method.name
        method_scope = f"{class_scope}.{method_name}"

        add_symbol(
            name=method_name,
            symbol_type="method",
            data_type=(
                self._get_type_name(method.return_type)
                if method.return_type
                else "void"
            ),
            scope=class_scope,
            line=method.position.line if method.position else None,
            modifiers=list(method.modifiers) if method.modifiers else [],
            annotations=self._extract_annotations(method),
        )

        if method.parameters:
            for param in method.parameters:
                add_symbol(
                    name=param.name,
                    symbol_type="parameter",
                    data_type=self._get_type_name(param.type),
                    scope=method_scope,
                )
