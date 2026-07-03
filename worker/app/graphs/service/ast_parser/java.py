"""Java AST parsing service.

This module defines the `JavaParsingService` class, which implements the
`ParsingService` interface for parsing Java source code. It uses the `javalang`
library to parse Java code and extract relevant AST structures such as packages,
imports, classes, interfaces, enums, fields, methods, and annotations.
"""

import hashlib
from collections.abc import Callable
from typing import Any

import javalang
from javalang.tree import (
    Assignment,
    BlockStatement,
    ClassCreator,
    ClassDeclaration,
    CompilationUnit,
    EnumDeclaration,
    ForStatement,
    IfStatement,
    InterfaceDeclaration,
    LocalVariableDeclaration,
    MemberReference,
    MethodInvocation,
    ReturnStatement,
    StatementExpression,
    This,
    TryStatement,
    WhileStatement,
)

from app.graphs.service.ast_parser.base import ASTParserService


class JavaASTParserService(ASTParserService):
    """Parse Java source code and extract AST structures."""

    _EXPR_KIND_MAP: dict[type, str] = {
        Assignment: "Assignment",
        MethodInvocation: "MethodInvocation",
        ClassCreator: "ClassCreator",
        This: "FieldAccess",
    }

    _JAVA_LANG_TYPES = frozenset(
        {
            "Object",
            "String",
            "Integer",
            "Long",
            "Double",
            "Float",
            "Boolean",
            "Byte",
            "Short",
            "Character",
            "Number",
            "Math",
            "System",
            "Thread",
            "Runnable",
            "Comparable",
            "Iterable",
            "Class",
            "Enum",
            "Throwable",
            "Exception",
            "RuntimeException",
            "Error",
            "StringBuilder",
            "StringBuffer",
            "Override",
            "Deprecated",
            "SuppressWarnings",
            "FunctionalInterface",
        }
    )

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

        total_lines = source_code.count("\n") + 1
        package = tree.package.name if tree.package else None
        imports, import_map = self._extract_imports(tree)

        return {
            "file": file_path,
            "key": f"file:{file_path}",
            "package": package,
            "imports": imports,
            "import_map": import_map,
            "classes": self._extract_classes(
                tree, file_path, package, import_map, total_lines
            ),
            "interfaces": self._extract_interfaces(
                tree, file_path, package, import_map, total_lines
            ),
            "enums": self._extract_enums(tree, file_path, package, total_lines),
        }

    def extract_method_contexts(self, source_code: str) -> dict[str, dict[str, Any]]:
        """Extract per-owner canonical context for graph builders that need
        more than method keys (the Java DFG builder).

        The richer sibling of `extract_method_keys`: same parse, iteration order
        and simple-name keying, so positional alignment with a javalang traversal
        and the same-simple-name collapse are identical. It additionally exposes
        the owner FQN, each method's resolved parameters, and the canonical field
        keys/types — everything required to build canonical `var_key`s — all from
        the same single-source helpers (`_method_key_for`, `_field_key_for`,
        `_extract_parameters`, `_resolve_type`) so the DFG never re-derives them.

        CFG only needs the keys and so uses the lighter `extract_method_keys`;
        the DFG uses this when it also needs field/param identity.

        Args:
            source_code(str): The raw Java source code.

        Returns:
            dict[str, dict[str, Any]]: Mapping of class/interface simple name to a
                context dict with `owner_fqn`, an ordered `methods` list (each with
                `key` and resolved `parameters`), and a `fields` map keyed by simple
                field name (each with canonical `key`, `owner_fqn`, `type_simple`,
                `type_fqn`).

        Raises:
            Exception: If parsing fails.
        """
        tree = javalang.parse.parse(source_code)
        package = tree.package.name if tree.package else None
        _, import_map = self._extract_imports(tree)

        contexts: dict[str, dict[str, Any]] = {}
        for node_type in (ClassDeclaration, InterfaceDeclaration):
            for _, node in tree.filter(node_type):
                owner_fqn = f"{package}.{node.name}" if package else node.name
                methods = [
                    {
                        "key": self._method_key_for(
                            method, owner_fqn, import_map, package
                        ),
                        "parameters": self._extract_parameters(
                            method, import_map, package
                        ),
                    }
                    for method in (node.methods or [])
                ]
                fields: dict[str, dict[str, Any]] = {}
                for field in getattr(node, "fields", None) or []:
                    type_simple = self._get_type_name(field.type)
                    type_fqn = self._resolve_type(type_simple, import_map, package)
                    for declarator in field.declarators:
                        fields[declarator.name] = {
                            "key": self._field_key_for(
                                owner_fqn, declarator.name, type_fqn
                            ),
                            "owner_fqn": owner_fqn,
                            "type_simple": type_simple,
                            "type_fqn": type_fqn,
                        }
                contexts[node.name] = {
                    "owner_fqn": owner_fqn,
                    "methods": methods,
                    "fields": fields,
                }
        return contexts

    def extract_method_keys(self, source_code: str) -> dict[str, list[str]]:
        """Extract canonical method keys without the full AST extraction.

        A lighter entry point than `parse()` for consumers (the Java CFG/DFG
        builders) that only need the canonical, overload-safe method keys to use
        as their graph `scope`. The keys are produced by the same
        `_method_key_for` path as `parse()`, so they are byte-identical — the
        AST parser remains the single identity authority for Java.

        Keys are listed in declaration order per owner so callers can map them
        back to javalang method nodes by position (overload-safe). All methods
        are included, including abstract/bodyless ones, to preserve that alignment.

        Args:
            source_code(str): The raw Java source code.

        Returns:
            dict[str, list[str]]: Mapping of class/interface simple name to its
                ordered list of canonical method keys.

        Raises:
            Exception: If parsing fails.
        """
        tree = javalang.parse.parse(source_code)
        package = tree.package.name if tree.package else None
        _, import_map = self._extract_imports(tree)

        key_map: dict[str, list[str]] = {}
        for node_type in (ClassDeclaration, InterfaceDeclaration):
            for _, node in tree.filter(node_type):
                owner_fqn = f"{package}.{node.name}" if package else node.name
                key_map[node.name] = [
                    self._method_key_for(method, owner_fqn, import_map, package)
                    for method in (node.methods or [])
                ]
        return key_map

    def _compute_stmt_key(
        self, file_path: str, line_start: int, kind: str, text: str
    ) -> str:
        """Compute a stable statement key using SHA1.

        Args:
            file_path(str): The relative file path.
            line_start(int): The line number.
            kind(str): The statement kind (lowercase).
            text(str): The statement text.

        Returns:
            str: A stable key of the form `stmt:<path>:<line>:<kind>:<hash>`.
        """
        text_hash = hashlib.sha1(
            text.encode("utf-8", errors="replace"), usedforsecurity=False
        ).hexdigest()[:8]
        return f"stmt:{file_path}:{line_start}:{kind}:{text_hash}"

    def _estimate_line_end(
        self,
        nodes_list: list[tuple[Any, Any]],
        current_idx: int,
        total_lines: int,
    ) -> int | None:
        """Estimate line_end for a type declaration using next-sibling position.

        Args:
            nodes_list: List of (path, node) tuples from tree.filter().
            current_idx(int): Index of the current node.
            total_lines(int): Total line count of the source file.

        Returns:
            int | None: The estimated end line.
        """
        if current_idx + 1 < len(nodes_list):
            next_line = self._get_node_line(nodes_list[current_idx + 1][1])
            if next_line is not None:
                return next_line - 1
        return total_lines

    def _estimate_method_line_end(
        self,
        methods_list: list[Any],
        current_idx: int,
        owner_line_end: int | None,
        total_lines: int,
    ) -> int | None:
        """Estimate line_end for a method using next-sibling position.

        Args:
            methods_list: List of method nodes.
            current_idx(int): Index of the current method.
            owner_line_end(int | None): The owning type's line_end.
            total_lines(int): Total line count of the source file.

        Returns:
            int | None: The estimated end line.
        """
        if current_idx + 1 < len(methods_list):
            next_line = self._get_node_line(methods_list[current_idx + 1])
            if next_line is not None:
                return next_line - 1
        return owner_line_end or total_lines

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

    def _extract_classes(
        self,
        tree: CompilationUnit,
        file_path: str,
        package: str | None,
        import_map: dict[str, str],
        total_lines: int,
    ) -> list[dict[str, Any]]:
        """Extract class declarations.

        Args:
            tree(CompilationUnit): The parsed Java AST.
            file_path(str): The relative file path.
            package(str | None): The package name.
            import_map(dict[str, str]): Simple name to FQN mapping.
            total_lines(int): Total line count for line_end estimation.

        Returns:
            list[dict[str, Any]]: A list of class declaration dictionaries.
        """

        def extra(node: Any, fqn: str) -> dict[str, Any]:
            return {
                "extends": node.extends.name if node.extends else None,
                "implements": (
                    [impl.name for impl in node.implements] if node.implements else []
                ),
                "fields": self._extract_fields(
                    node, fqn, file_path, import_map, package
                ),
                "methods": self._extract_methods(
                    node, fqn, file_path, import_map, package, total_lines
                ),
            }

        return self._extract_type_declarations(
            tree, ClassDeclaration, "class", file_path, package, total_lines, extra
        )

    def _extract_enums(
        self,
        tree: CompilationUnit,
        file_path: str,
        package: str | None,
        total_lines: int,
    ) -> list[dict[str, Any]]:
        """Extract enum declarations.

        Args:
            tree(CompilationUnit): The parsed Java AST.
            file_path(str): The relative file path.
            package(str | None): The package name.
            total_lines(int): Total line count for line_end estimation.

        Returns:
            list[dict[str, Any]]: A list of enum declaration dictionaries.
        """

        def extra(node: Any, _fqn: str) -> dict[str, Any]:
            return {
                "implements": (
                    [impl.name for impl in node.implements] if node.implements else []
                ),
                "body": (
                    [const.name for const in node.body.constants]
                    if node.body and node.body.constants
                    else []
                ),
            }

        return self._extract_type_declarations(
            tree, EnumDeclaration, "enum", file_path, package, total_lines, extra
        )

    def _extract_fields(
        self,
        class_node: Any,
        owner_fqn: str,
        file_path: str,
        import_map: dict[str, str],
        package: str | None,
    ) -> list[dict[str, Any]]:
        """Extract field declarations from a class.

        Args:
            class_node(Any): A javalang AST node representing a class.
            owner_fqn(str): The FQN of the owning type.
            file_path(str): The relative file path.
            import_map(dict[str, str]): Simple name to FQN mapping.
            package(str | None): The package name.

        Returns:
            list[dict[str, Any]]: A list of field declaration dictionaries.
        """
        fields = []
        for field in class_node.fields:
            type_simple = self._get_type_name(field.type)
            type_fqn = self._resolve_type(type_simple, import_map, package)
            line_start = self._get_node_line(field)

            for declarator in field.declarators:
                key = self._field_key_for(owner_fqn, declarator.name, type_fqn)
                fields.append(
                    {
                        "name": declarator.name,
                        "type": type_simple,
                        "type_simple": type_simple,
                        "type_fqn": type_fqn,
                        "owner_fqn": owner_fqn,
                        "key": key,
                        "modifiers": (list(field.modifiers) if field.modifiers else []),
                        "annotations": self._extract_annotations(field),
                        "source_ref": self._make_source_ref(
                            file_path, line_start, line_start
                        ),
                        "line_start": line_start,
                        "confidence": 1.0 if type_fqn != type_simple else 0.5,
                        "source": "ast",
                    }
                )
        return fields

    def _extract_imports(
        self, tree: CompilationUnit
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Extract import statements and build an import_map.

        Args:
            tree(CompilationUnit): The parsed Java AST.

        Returns:
            tuple: (imports list, import_map mapping simple names to FQN).
        """
        imports: list[dict[str, Any]] = []
        import_map: dict[str, str] = {}

        for imp in tree.imports:
            imports.append(
                {
                    "path": imp.path,
                    "static": imp.static,
                    "wildcard": imp.wildcard,
                }
            )
            if not imp.wildcard:
                simple_name = imp.path.rsplit(".", 1)[-1]
                import_map[simple_name] = imp.path

        return imports, import_map

    def _extract_interfaces(
        self,
        tree: CompilationUnit,
        file_path: str,
        package: str | None,
        import_map: dict[str, str],
        total_lines: int,
    ) -> list[dict[str, Any]]:
        """Extract interface declarations.

        Args:
            tree(CompilationUnit): The parsed Java AST.
            file_path(str): The relative file path.
            package(str | None): The package name.
            import_map(dict[str, str]): Simple name to FQN mapping.
            total_lines(int): Total line count for line_end estimation.

        Returns:
            list[dict[str, Any]]: A list of interface declaration dictionaries.
        """

        def extra(node: Any, fqn: str) -> dict[str, Any]:
            return {
                "extends": ([ext.name for ext in node.extends] if node.extends else []),
                "methods": self._extract_methods(
                    node, fqn, file_path, import_map, package, total_lines
                ),
            }

        return self._extract_type_declarations(
            tree,
            InterfaceDeclaration,
            "interface",
            file_path,
            package,
            total_lines,
            extra,
        )

    def _extract_methods(
        self,
        class_node: Any,
        owner_fqn: str,
        file_path: str,
        import_map: dict[str, str],
        package: str | None,
        total_lines: int,
    ) -> list[dict[str, Any]]:
        """Extract method declarations from a class or interface.

        Args:
            class_node(Any): A javalang AST node representing a class
                or interface.
            owner_fqn(str): The FQN of the owning type.
            file_path(str): The relative file path.
            import_map(dict[str, str]): Simple name to FQN mapping.
            package(str | None): The package name.
            total_lines(int): Total line count for line_end estimation.

        Returns:
            list[dict[str, Any]]: A list of method declaration dictionaries.
        """
        methods = []
        method_nodes = (
            list(class_node.methods)
            if hasattr(class_node, "methods") and class_node.methods
            else []
        )

        for idx, method in enumerate(method_nodes):
            throws = self._extract_throws(method)
            params = self._extract_parameters(method, import_map, package)

            return_type_simple = (
                self._get_type_name(method.return_type)
                if method.return_type
                else "void"
            )
            return_type_fqn = self._resolve_type(
                return_type_simple, import_map, package
            )

            param_types_simple = ",".join(p["type_simple"] for p in params)
            param_types_resolved = ",".join(p["type_fqn"] for p in params)
            signature_simple = f"{method.name}({param_types_simple})"
            signature_resolved = f"{method.name}({param_types_resolved})"
            method_key = self._method_key_for(method, owner_fqn, import_map, package)

            line_start = self._get_node_line(method)
            line_end = self._estimate_method_line_end(
                method_nodes, idx, None, total_lines
            )

            methods.append(
                {
                    "name": method.name,
                    "owner_fqn": owner_fqn,
                    "return_type_fqn": return_type_fqn,
                    "signature_simple": signature_simple,
                    "signature_resolved": signature_resolved,
                    "key": method_key,
                    "modifiers": (list(method.modifiers) if method.modifiers else []),
                    "parameters": params,
                    "annotations": self._extract_annotations(method),
                    "throws": throws,
                    "statements": self._extract_statements(method, file_path),
                    "source_ref": self._make_source_ref(
                        file_path, line_start, line_end
                    ),
                    "line_start": line_start,
                    "confidence": 1.0,
                    "source": "ast",
                }
            )
        return methods

    def _method_key_for(
        self,
        method: Any,
        owner_fqn: str,
        import_map: dict[str, str],
        package: str | None,
    ) -> str:
        """Build the canonical, overload-safe key for a single method.

        Single source of truth for the method key string — both `parse()`
        (via `_extract_methods`) and `extract_method_keys` go through here,
        so the keys they produce are byte-identical.

        Args:
            method(Any): A javalang AST node representing a method.
            owner_fqn(str): The FQN of the owning type.
            import_map(dict[str, str]): Simple name to FQN mapping.
            package(str | None): The package name.

        Returns:
            str: A key of the form
                `method:<ownerFQN>#<name>(<paramTypesFQN>):<returnTypeFQN>`.
        """
        param_types_resolved = ",".join(
            p["type_fqn"] for p in self._extract_parameters(method, import_map, package)
        )
        return_type_simple = (
            self._get_type_name(method.return_type) if method.return_type else "void"
        )
        return_type_fqn = self._resolve_type(return_type_simple, import_map, package)
        return (
            f"method:{owner_fqn}#{method.name}"
            f"({param_types_resolved}):{return_type_fqn}"
        )

    def _field_key_for(self, owner_fqn: str, name: str, type_fqn: str) -> str:
        """Build the canonical key for a single field.

        Single source of truth for the field key string — both `parse()`
        (via `_extract_fields`) and `extract_method_contexts` go through here,
        so the keys they produce are byte-identical.

        Args:
            owner_fqn(str): The FQN of the owning type.
            name(str): The field (declarator) name.
            type_fqn(str): The resolved type FQN.

        Returns:
            str: A key of the form `field:<ownerFQN>#<name>:<typeFQN>`.
        """
        return f"field:{owner_fqn}#{name}:{type_fqn}"

    def _extract_parameters(
        self,
        method: Any,
        import_map: dict[str, str],
        package: str | None,
    ) -> list[dict[str, Any]]:
        """Extract method parameters with type resolution.

        Args:
            method(Any): A javalang AST node representing a method.
            import_map(dict[str, str]): Simple name to FQN mapping.
            package(str | None): The package name.

        Returns:
            list[dict[str, Any]]: A list of parameter dictionaries.
        """
        if not method.parameters:
            return []

        parameters = []
        for param in method.parameters:
            type_simple = self._get_type_name(param.type)
            type_fqn = self._resolve_type(type_simple, import_map, package)
            parameters.append(
                {
                    "name": param.name,
                    "type": type_simple,
                    "type_simple": type_simple,
                    "type_fqn": type_fqn,
                    "varargs": getattr(param, "varargs", False),
                }
            )
        return parameters

    def _extract_statements(self, method: Any, file_path: str) -> list[dict[str, Any]]:
        """Extract statement-level data from a method body.

        Args:
            method(Any): A javalang AST node representing a method.
            file_path(str): The relative file path.

        Returns:
            list[dict[str, Any]]: A list of statement dictionaries.
        """
        if not method.body:
            return []

        statements: list[dict[str, Any]] = []
        self._walk_statements(method.body, file_path, statements)
        return statements

    def _extract_throws(self, method: Any) -> list[str]:
        """Extract throws clause from a method.

        Args:
            method(Any): A javalang AST node representing a method.

        Returns:
            list[str]: A list of exception type names.
        """
        if not method.throws:
            return []

        throws = []
        for exc in method.throws:
            if isinstance(exc, str):
                throws.append(exc)
            elif hasattr(exc, "name"):
                throws.append(exc.name)
            else:
                throws.append(str(exc))
        return throws

    def _extract_type_declarations(
        self,
        tree: CompilationUnit,
        node_type: type,
        type_label: str,
        file_path: str,
        package: str | None,
        total_lines: int,
        extra_fields_fn: Callable[[Any, str], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Shared skeleton for extracting class/interface/enum declarations.

        Args:
            tree(CompilationUnit): The parsed Java AST.
            node_type(type): The javalang node type to filter for.
            type_label(str): The label for the type (e.g. "class").
            file_path(str): The relative file path.
            package(str | None): The package name.
            total_lines(int): Total line count for line_end estimation.
            extra_fields_fn(Callable): A callback that receives (node, fqn)
                and returns a dict of type-specific fields.

        Returns:
            list[dict[str, Any]]: A list of type declaration dictionaries.
        """
        nodes = list(tree.filter(node_type))
        result: list[dict[str, Any]] = []

        for idx, (_path, node) in enumerate(nodes):
            line_start = self._get_node_line(node)
            line_end = self._estimate_line_end(nodes, idx, total_lines)
            fqn = f"{package}.{node.name}" if package else node.name

            data = {
                "name": node.name,
                "type": type_label,
                "fqn": fqn,
                "key": f"type:{fqn}",
                "modifiers": list(node.modifiers) if node.modifiers else [],
                "annotations": self._extract_annotations(node),
                **extra_fields_fn(node, fqn),
                "source_ref": self._make_source_ref(file_path, line_start, line_end),
                "line_start": line_start,
            }
            result.append(data)

        return result

    def _get_node_line(self, node: Any) -> int | None:
        """Safely extract line number from a javalang node.

        Args:
            node(Any): A javalang AST node.

        Returns:
            int | None: The line number, or None if unavailable.
        """
        if hasattr(node, "position") and node.position:
            return int(node.position.line)
        return None

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

    def _make_source_ref(
        self,
        file_path: str,
        line_start: int | None,
        line_end: int | None,
    ) -> dict[str, Any]:
        """Build a source_ref dictionary.

        Args:
            file_path(str): The relative file path.
            line_start(int | None): Start line.
            line_end(int | None): End line.

        Returns:
            dict[str, Any]: A source reference dictionary.
        """
        return {
            "file": file_path,
            "line_start": line_start,
            "line_end": line_end,
            "col_start": None,
            "col_end": None,
        }

    def _make_stmt_entry(
        self, kind: str, text: str, line: int | None, file_path: str
    ) -> dict[str, Any]:
        """Create a statement entry dict.

        Args:
            kind(str): The statement kind (e.g. "ReturnStatement").
            text(str): Normalized text representation.
            line(int | None): The source line number.
            file_path(str): The relative file path.

        Returns:
            dict[str, Any]: A statement entry dictionary.
        """
        return {
            "kind": kind,
            "text": text,
            "source_ref": {
                "file": file_path,
                "line_start": line,
                "line_end": line,
            },
            "stmt_key": self._compute_stmt_key(
                file_path, line or 0, kind.lower(), text
            ),
        }

    def _resolve_type(
        self,
        simple_type: str,
        import_map: dict[str, str],
        package: str | None,
    ) -> str:
        """Resolve a simple type name to FQN via import_map (best-effort).

        Args:
            simple_type(str): The simple type name.
            import_map(dict[str, str]): Simple name to FQN mapping.
            package(str | None): The package name.

        Returns:
            str: The resolved FQN or the original type name.
        """
        base_type = simple_type.split("<")[0].split("[")[0]
        if base_type in import_map:
            return import_map[base_type]
        if base_type[0].islower():
            return simple_type
        if base_type in self._JAVA_LANG_TYPES:
            return f"java.lang.{base_type}"
        if package:
            return f"{package}.{base_type}"
        return simple_type

    def _stmt_text(self, node: Any) -> str:
        """Get a normalized text representation of a statement/expression.

        Args:
            node(Any): A javalang AST node.

        Returns:
            str: Best-effort text representation.
        """
        try:
            if isinstance(node, ReturnStatement):
                if node.expression:
                    return f"return {self._stmt_text(node.expression)}"
                return "return"

            if isinstance(node, Assignment):
                left = self._stmt_text(node.expressionl)
                right = self._stmt_text(node.value)
                return f"{left} = {right}"

            if isinstance(node, MethodInvocation):
                qualifier = (
                    f"{self._stmt_text(node.qualifier)}." if node.qualifier else ""
                )
                args = ", ".join(self._stmt_text(a) for a in (node.arguments or []))
                return f"{qualifier}{node.member}({args})"

            if isinstance(node, ClassCreator):
                type_name = (
                    node.type.name if hasattr(node.type, "name") else str(node.type)
                )
                args = ", ".join(self._stmt_text(a) for a in (node.arguments or []))
                return f"new {type_name}({args})"

            if isinstance(node, MemberReference):
                if node.qualifier:
                    return f"{self._stmt_text(node.qualifier)}.{node.member}"
                return str(node.member)

            if isinstance(node, This):
                parts = "this"
                if hasattr(node, "selectors") and node.selectors:
                    for sel in node.selectors:
                        if isinstance(sel, MethodInvocation):
                            args = ", ".join(
                                self._stmt_text(a) for a in (sel.arguments or [])
                            )
                            parts += f".{sel.member}({args})"
                        elif isinstance(sel, MemberReference):
                            parts += f".{sel.member}"
                return parts

            if isinstance(node, LocalVariableDeclaration):
                type_name = (
                    node.type.name if hasattr(node.type, "name") else str(node.type)
                )
                decls = []
                for d in node.declarators:
                    if d.initializer:
                        decls.append(f"{d.name} = {self._stmt_text(d.initializer)}")
                    else:
                        decls.append(d.name)
                return f"{type_name} {', '.join(decls)}"

            if hasattr(node, "value"):
                return str(node.value) if node.value is not None else "null"

            if isinstance(node, str):
                return node

            return type(node).__name__

        except Exception:
            return type(node).__name__ if node else ""

    def _walk_statements(
        self,
        stmts: Any,
        file_path: str,
        result: list[dict[str, Any]],
    ) -> None:
        """Recursively walk statements and extract pertinent ones.

        Args:
            stmts: A list of javalang statement nodes.
            file_path(str): The relative file path.
            result(list): Accumulator list for statement entries.
        """
        if not stmts:
            return

        for stmt in stmts:
            if stmt is None:
                continue

            if isinstance(stmt, ReturnStatement):
                text = self._stmt_text(stmt)
                line = self._get_node_line(stmt)
                result.append(
                    self._make_stmt_entry("ReturnStatement", text, line, file_path)
                )

            elif isinstance(stmt, StatementExpression):
                expr = stmt.expression
                kind = self._EXPR_KIND_MAP.get(type(expr), "Expression")
                text = self._stmt_text(expr)
                if kind != "Expression" or text:
                    line = self._get_node_line(stmt)
                    result.append(self._make_stmt_entry(kind, text, line, file_path))

            elif isinstance(stmt, LocalVariableDeclaration):
                text = self._stmt_text(stmt)
                line = self._get_node_line(stmt)
                result.append(
                    self._make_stmt_entry("LocalVarDecl", text, line, file_path)
                )

            # Recurse into compound statements
            if isinstance(stmt, IfStatement):
                self._walk_statements([stmt.then_statement], file_path, result)
                if stmt.else_statement:
                    self._walk_statements([stmt.else_statement], file_path, result)
            elif isinstance(stmt, (WhileStatement, ForStatement)):
                self._walk_statements([stmt.body], file_path, result)
            elif isinstance(stmt, BlockStatement):
                self._walk_statements(stmt.statements, file_path, result)
            elif isinstance(stmt, TryStatement):
                self._walk_statements(stmt.block, file_path, result)
                if stmt.catches:
                    for catch in stmt.catches:
                        self._walk_statements(catch.block, file_path, result)
                if stmt.finally_block:
                    self._walk_statements(stmt.finally_block, file_path, result)
