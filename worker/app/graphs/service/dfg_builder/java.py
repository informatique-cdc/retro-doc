"""Java DFG building service.

This module builds Data Flow Graphs for Java methods by parsing
source code with javalang and tracking variable definitions and uses.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Any

import javalang
import networkx as nx
from javalang.tree import (
    Assignment,
    BinaryOperation,
    BlockStatement,
    Cast,
    ClassCreator,
    ClassDeclaration,
    ForStatement,
    IfStatement,
    InterfaceDeclaration,
    LocalVariableDeclaration,
    MemberReference,
    MethodInvocation,
    ReturnStatement,
    StatementExpression,
    TernaryExpression,
    This,
    TryStatement,
    WhileStatement,
)
from loguru import logger

from app.graphs.service.ast_parser import JavaASTParserService
from app.graphs.service.dfg_builder.base import DFGBuilderService


@dataclass
class _DFGState:
    """Per-method DFG build state.

    Bundles the mutable graph state with the canonical metadata for one method.
    Created per method (never stored on the service, which is a shared singleton)
    and threaded through the analysis helpers.

    Attributes:
        graph(nx.DiGraph): The data flow graph being built.
        counter(list[int]): A single-item list used to generate unique node IDs.
        defs(dict[str, list[int]]): var_key -> definition node IDs.
        uses(dict[str, list[int]]): var_key -> use node IDs.
        last_def(dict[str, int]): var_key -> most recent definition node ID.
        last_def_by_name(dict[str, int]): variable name -> last def node ID (fallback).
        variables(dict[str, list[int]]): variable name -> all node IDs (for metrics).
        file_path(str): The file path for source_ref/stmt_key.
        method_key(str): The canonical method key (DFG scope, var_key prefix).
        owner_fqn(str): The FQN of the owning type.
        class_fields(dict[str, dict[str, Any]]): Field name -> AST field context
            (canonical key, owner_fqn, type_simple, type_fqn).
    """

    graph: nx.DiGraph
    counter: list[int]
    file_path: str
    method_key: str
    owner_fqn: str
    class_fields: dict[str, dict[str, Any]]
    defs: dict[str, list[int]] = field(default_factory=dict)
    uses: dict[str, list[int]] = field(default_factory=dict)
    last_def: dict[str, int] = field(default_factory=dict)
    last_def_by_name: dict[str, int] = field(default_factory=dict)
    variables: dict[str, list[int]] = field(default_factory=dict)


class JavaDFGBuilderService(DFGBuilderService):
    """Build data flow graphs for Java methods."""

    def build(self, source_code: str, file_path: str) -> list[dict[str, Any]]:
        """Build DFGs for all methods in a Java file.

        Args:
            source_code(str): The raw Java source code.
            file_path(str): The file path for metadata/logging purposes.

        Returns:
            list[dict[str, Any]]: List of dicts, one per method DFG.

        Raises:
            Exception: If parsing fails.
        """
        tree = javalang.parse.parse(source_code)

        # Reuse the AST parser to obtain canonical, overload-safe method keys, owner
        # FQNs and field keys/types without duplicating its type-resolution logic.
        # Context is mapped back to methods by position, since the AST parser iterates
        # the same parsed tree in the same order.
        contexts = self._build_context_map(source_code, file_path)

        results: list[dict[str, Any]] = []

        for _, node in tree.filter(ClassDeclaration):
            if not node.methods:
                continue
            ctx = contexts.get(node.name, {})
            owner_fqn = ctx.get("owner_fqn", node.name)
            method_ctxs = ctx.get("methods", [])
            class_fields = ctx.get("fields", {})
            for idx, method in enumerate(node.methods):
                method_ctx = method_ctxs[idx] if idx < len(method_ctxs) else None
                scope = method_ctx["key"] if method_ctx else ""
                if not method_ctx or not scope:
                    logger.warning(
                        f"Graphs(DFG Java): DFG skipped, no canonical key for "
                        f"'{node.name}.{method.name}' in '{file_path}'"
                    )
                    continue
                dfg = self._build_method_dfg(
                    method,
                    scope,
                    owner_fqn,
                    method_ctx["parameters"],
                    class_fields,
                    file_path,
                )
                results.append(dfg)

        for _, node in tree.filter(InterfaceDeclaration):
            if not node.methods:
                continue
            ctx = contexts.get(node.name, {})
            owner_fqn = ctx.get("owner_fqn", node.name)
            method_ctxs = ctx.get("methods", [])
            for idx, method in enumerate(node.methods):
                if not method.body:
                    continue
                method_ctx = method_ctxs[idx] if idx < len(method_ctxs) else None
                scope = method_ctx["key"] if method_ctx else ""
                if not method_ctx or not scope:
                    logger.warning(
                        f"Graphs(DFG Java): DFG skipped, no canonical key for "
                        f"'{node.name}.{method.name}' in '{file_path}'"
                    )
                    continue
                dfg = self._build_method_dfg(
                    method,
                    scope,
                    owner_fqn,
                    method_ctx["parameters"],
                    {},
                    file_path,
                )
                results.append(dfg)

        return results

    def _build_context_map(
        self, source_code: str, file_path: str
    ) -> dict[str, dict[str, Any]]:
        """Build a map of owner name to its canonical method/field context.

        Delegates to `JavaASTParserService.extract_method_contexts` so the DFG
        `scope` and `var_key`s share the AST's canonical
        `method:<ownerFQN>#<name>(<paramFQNs>):<retFQN>` and
        `field:<ownerFQN>#<name>:<typeFQN>` keys. Java's graph builders share the
        Java AST parser's canonical keys as the single identity authority.

        Args:
            source_code(str): The raw Java source code.
            file_path(str): The file path used for AST metadata.

        Returns:
            dict[str, dict[str, Any]]: Mapping of class/interface name to its
                context (owner_fqn, ordered methods, fields). Empty on failure.
        """
        try:
            return JavaASTParserService().extract_method_contexts(source_code)
        except Exception:
            logger.exception(
                f"Graphs(DFG Java): AST context extraction failed for '{file_path}'"
            )
            return {}

    # ------------------------------------------------------------------
    # Node / edge construction
    # ------------------------------------------------------------------

    def _add_node(
        self,
        state: _DFGState,
        variable: str,
        var_kind: str,
        operation: str,
        *,
        line: int | None = None,
        var_key: str = "",
        source_ref: dict[str, Any] | None = None,
        stmt_key: str | None = None,
        owner_fqn: str | None = None,
        type_simple: str | None = None,
        type_fqn: str | None = None,
    ) -> int:
        """Add a node to the graph and update defs/uses tracking.

        Args:
            state(_DFGState): The per-method build state.
            variable(str): The variable name associated with this node.
            var_kind(str): The variable kind ("param", "field", "local").
            operation(str): The operation ("def", "use", "write", "read", "init").
            line(int | None): The source line, if available.
            var_key(str): The canonical var_key.
            source_ref(dict[str, Any] | None): Source reference metadata.
            stmt_key(str | None): Statement key, if available.
            owner_fqn(str | None): The owning type FQN (fields only).
            type_simple(str | None): The simple type name, if known.
            type_fqn(str | None): The resolved type FQN, if known.

        Returns:
            int: The ID of the newly added node.
        """
        nid = state.counter[0]
        state.counter[0] += 1
        attrs: dict[str, Any] = {
            "id": nid,
            "variable": variable,
            "type": var_kind,
            "operation": operation,
            "var_key": var_key,
            "var_kind": var_kind,
        }
        if source_ref is not None:
            attrs["source_ref"] = source_ref
        if stmt_key is not None:
            attrs["stmt_key"] = stmt_key
        if line is not None:
            attrs["line"] = line
        if owner_fqn is not None:
            attrs["owner_fqn"] = owner_fqn
        if type_simple is not None:
            attrs["type_simple"] = type_simple
        if type_fqn is not None:
            attrs["type_fqn"] = type_fqn
        state.graph.add_node(nid, **attrs)

        state.variables.setdefault(variable, []).append(nid)

        effective_key = var_key or variable
        if operation in ("def", "write", "init"):
            state.defs.setdefault(effective_key, []).append(nid)
            state.last_def[effective_key] = nid
            state.last_def_by_name[variable] = nid
        elif operation in ("use", "read"):
            state.uses.setdefault(effective_key, []).append(nid)
        return nid

    def _add_var_node(
        self,
        state: _DFGState,
        var_name: str,
        operation: str,
        stmt: Any = None,
        stmt_text: str = "",
    ) -> int:
        """Add a variable node with full canonical metadata.

        Args:
            state(_DFGState): The per-method build state.
            var_name(str): The variable name.
            operation(str): The operation ("use", "write", ...).
            stmt(Any): The javalang node for the source position, if any.
            stmt_text(str): Optional text used for the stmt_key.

        Returns:
            int: The ID of the newly added node.
        """
        var_kind = self._var_kind(var_name, state.class_fields)
        var_key = self._make_var_key(state, var_name, var_kind)
        line = self._get_node_line(stmt) if stmt is not None else None
        source_ref = (
            self._make_source_ref(state.file_path, line) if line is not None else None
        )
        stmt_key = self._make_stmt_key(
            state.file_path, operation, stmt_text or var_name, line
        )

        owner_fqn: str | None = None
        type_simple: str | None = None
        type_fqn: str | None = None
        if var_kind == "field" and var_name in state.class_fields:
            field_ctx = state.class_fields[var_name]
            owner_fqn = field_ctx.get("owner_fqn")
            type_simple = field_ctx.get("type_simple")
            type_fqn = field_ctx.get("type_fqn")

        return self._add_node(
            state,
            var_name,
            var_kind,
            operation,
            line=line,
            var_key=var_key,
            source_ref=source_ref,
            stmt_key=stmt_key,
            owner_fqn=owner_fqn,
            type_simple=type_simple,
            type_fqn=type_fqn,
        )

    def _add_edge(
        self,
        state: _DFGState,
        src: int,
        dst: int,
        edge_type: str = "flow",
        var_key: str = "",
        confidence: float = 1.0,
    ) -> None:
        """Add an edge with enriched metadata if it doesn't already exist.

        Args:
            state(_DFGState): The per-method build state.
            src(int): The source node ID.
            dst(int): The destination node ID.
            edge_type(str): The edge type ("flow", "def-use", "init").
            var_key(str): The canonical var_key the edge carries.
            confidence(float): The edge confidence (default 1.0).
        """
        if not state.graph.has_edge(src, dst):
            state.graph.add_edge(
                src, dst, type=edge_type, var_key=var_key, confidence=confidence
            )

    def _connect_def_to_use(
        self, state: _DFGState, variable: str, use_nid: int
    ) -> None:
        """Connect the most recent definition of a variable to a new use node.

        Resolves by canonical var_key first, then falls back to the bare variable
        name (so parameter defs, keyed by `param:...`, still connect to body uses
        keyed by `local:...`).

        Args:
            state(_DFGState): The per-method build state.
            variable(str): The variable being used.
            use_nid(int): The use node ID to connect to its definition.
        """
        var_kind = self._var_kind(variable, state.class_fields)
        var_key = self._make_var_key(state, variable, var_kind)
        effective_key = var_key or variable
        if effective_key in state.last_def:
            self._add_edge(
                state, state.last_def[effective_key], use_nid, "def-use", effective_key
            )
        elif variable in state.last_def_by_name:
            self._add_edge(
                state,
                state.last_def_by_name[variable],
                use_nid,
                "def-use",
                effective_key,
            )

    # ------------------------------------------------------------------
    # Canonical key / metadata helpers
    # ------------------------------------------------------------------

    def _var_kind(self, var_name: str, class_fields: dict[str, dict[str, Any]]) -> str:
        """Determine the kind of a body variable (field vs local).

        Args:
            var_name(str): The variable name.
            class_fields(dict[str, dict[str, Any]]): The owner's field context.

        Returns:
            str: "field" if the name is a known field, else "local".
        """
        return "field" if var_name in class_fields else "local"

    def _make_var_key(self, state: _DFGState, var_name: str, var_kind: str) -> str:
        """Build the canonical var_key for a body variable.

        Fields reuse the AST's canonical field key (byte-identical to the AST
        field vertex id); locals are scoped by the canonical method key.

        Args:
            state(_DFGState): The per-method build state.
            var_name(str): The variable name.
            var_kind(str): The variable kind ("field" or "local").

        Returns:
            str: The canonical var_key.
        """
        if var_kind == "field" and var_name in state.class_fields:
            return str(state.class_fields[var_name]["key"])
        return f"local:{state.method_key}:0:{var_name}"

    def _get_node_line(self, node: Any) -> int | None:
        """Safely extract the line number from a javalang node.

        Args:
            node(Any): A javalang AST node.

        Returns:
            int | None: The line number, or None if unavailable.
        """
        if hasattr(node, "position") and node.position:
            return int(node.position.line)
        return None

    def _make_source_ref(self, file_path: str, line: int) -> dict[str, Any]:
        """Build a source_ref dictionary for a single-line node.

        Args:
            file_path(str): The file path.
            line(int): The source line number.

        Returns:
            dict[str, Any]: A source reference dictionary.
        """
        return {"file": file_path, "line_start": line, "line_end": line}

    def _compute_stmt_key(self, file_path: str, line: int, kind: str, text: str) -> str:
        """Compute a stable statement key (matches the AST parser/CFG builder).

        Args:
            file_path(str): The file path.
            line(int): The line number.
            kind(str): The statement/operation kind.
            text(str): The text to hash.

        Returns:
            str: A key of the form `stmt:<path>:<line>:<kind>:<hash>`.
        """
        text_hash = hashlib.sha1(
            text.encode("utf-8", errors="replace"), usedforsecurity=False
        ).hexdigest()[:8]
        return f"stmt:{file_path}:{line}:{kind}:{text_hash}"

    def _make_stmt_key(
        self, file_path: str, kind: str, text: str, line: int | None
    ) -> str | None:
        """Build a stmt_key, or None when the node has no source position.

        Args:
            file_path(str): The file path.
            kind(str): The statement/operation kind.
            text(str): The text to hash.
            line(int | None): The source line number, if available.

        Returns:
            str | None: The statement key, or None.
        """
        if line is None:
            return None
        return self._compute_stmt_key(file_path, line, kind, text)

    # ------------------------------------------------------------------
    # DFG construction
    # ------------------------------------------------------------------

    def _build_method_dfg(
        self,
        java_method: Any,
        scope: str,
        owner_fqn: str,
        parameters: list[dict[str, Any]],
        class_fields: dict[str, dict[str, Any]],
        file_path: str,
    ) -> dict[str, Any]:
        """Build a DFG dict for a single method.

        Args:
            java_method(Any): The javalang method node.
            scope(str): The canonical method key used as the document scope.
            owner_fqn(str): The FQN of the owning type.
            parameters(list[dict[str, Any]]): The AST-resolved parameters
                (name, type_simple, type_fqn).
            class_fields(dict[str, dict[str, Any]]): The owner's field context.
            file_path(str): The file path for node source_ref/stmt_key.

        Returns:
            dict[str, Any]: A dict with method name, canonical scope, nodes,
                edges, defs/uses (by var_key) and metrics.
        """
        state = _DFGState(
            graph=nx.DiGraph(),
            counter=[0],
            file_path=file_path,
            method_key=scope,
            owner_fqn=owner_fqn,
            class_fields=class_fields,
        )

        # Add parameter nodes as implicit definitions (canonical param var_key).
        for param in parameters:
            param_name = param["name"]
            var_key = f"param:{scope}:{param_name}"
            self._add_node(
                state,
                param_name,
                "param",
                "def",
                var_key=var_key,
                type_simple=param.get("type_simple"),
                type_fqn=param.get("type_fqn"),
            )

        if java_method.body:
            self._analyze_block(state, java_method.body)

        graph = state.graph
        return {
            "method_name": java_method.name,
            "scope": scope,
            "nodes": [{"id": nid, **graph.nodes[nid]} for nid in graph.nodes()],
            "edges": [
                {
                    "from": u,
                    "to": v,
                    "type": graph.edges[u, v].get("type", ""),
                    "var_key": graph.edges[u, v].get("var_key", ""),
                    "confidence": graph.edges[u, v].get("confidence", 1.0),
                }
                for u, v in graph.edges()
            ],
            "defs": state.defs,
            "uses": state.uses,
            "metrics": {
                "defs_count": sum(len(v) for v in state.defs.values()),
                "uses_count": sum(len(v) for v in state.uses.values()),
                "writes_count": sum(
                    1
                    for nid in graph.nodes()
                    if graph.nodes[nid].get("operation") == "write"
                ),
                "variables_count": len(state.variables),
            },
        }

    # ------------------------------------------------------------------
    # Statement analysis
    # ------------------------------------------------------------------

    def _analyze_block(self, state: _DFGState, statements: list[Any]) -> None:
        """Analyze a block of statements.

        Args:
            state(_DFGState): The per-method build state.
            statements(list[Any]): The statement nodes to analyze.
        """
        if not statements:
            return
        for stmt in statements:
            self._analyze_statement(state, stmt)

    def _analyze_statement(self, state: _DFGState, stmt: Any) -> None:
        """Analyze a single statement.

        Args:
            state(_DFGState): The per-method build state.
            stmt(Any): The statement node to analyze.
        """
        try:
            if isinstance(stmt, LocalVariableDeclaration):
                self._analyze_local_var_decl(state, stmt)
            elif isinstance(stmt, StatementExpression):
                self._analyze_expression(state, stmt.expression, stmt)
            elif isinstance(stmt, IfStatement):
                for var in self._extract_vars(stmt.condition, state.class_fields):
                    nid = self._add_var_node(state, var, "use", stmt)
                    self._connect_def_to_use(state, var, nid)
                self._analyze_statement(state, stmt.then_statement)
                if stmt.else_statement:
                    self._analyze_statement(state, stmt.else_statement)
            elif isinstance(stmt, WhileStatement):
                for var in self._extract_vars(stmt.condition, state.class_fields):
                    nid = self._add_var_node(state, var, "use", stmt)
                    self._connect_def_to_use(state, var, nid)
                self._analyze_statement(state, stmt.body)
            elif isinstance(stmt, ForStatement):
                if hasattr(stmt, "control") and stmt.control:
                    if hasattr(stmt.control, "init"):
                        self._analyze_expression(state, stmt.control.init, stmt)
                    if hasattr(stmt.control, "condition"):
                        self._analyze_expression(state, stmt.control.condition, stmt)
                self._analyze_statement(state, stmt.body)
            elif isinstance(stmt, ReturnStatement):
                if stmt.expression:
                    for var in self._extract_vars(stmt.expression, state.class_fields):
                        nid = self._add_var_node(state, var, "use", stmt)
                        self._connect_def_to_use(state, var, nid)
            elif isinstance(stmt, BlockStatement):
                self._analyze_block(state, stmt.statements)
            elif isinstance(stmt, TryStatement):
                self._analyze_block(state, stmt.block)
                if stmt.catches:
                    for catch in stmt.catches:
                        self._analyze_block(state, catch.block)
                if stmt.finally_block:
                    self._analyze_block(state, stmt.finally_block)
        except Exception:
            logger.exception(
                f"Graphs(DFG Java): Error analyzing statement {type(stmt).__name__}"
            )

    def _analyze_local_var_decl(
        self, state: _DFGState, stmt: LocalVariableDeclaration
    ) -> None:
        """Analyze a local variable declaration, adding def nodes and init edges.

        Args:
            state(_DFGState): The per-method build state.
            stmt(LocalVariableDeclaration): The declaration to analyze.
        """
        line = self._get_node_line(stmt)
        source_ref = (
            self._make_source_ref(state.file_path, line) if line is not None else None
        )
        type_simple = stmt.type.name if hasattr(stmt.type, "name") else str(stmt.type)

        for declarator in stmt.declarators:
            var_name = declarator.name
            var_key = f"local:{state.method_key}:0:{var_name}"
            def_nid = self._add_node(
                state,
                var_name,
                "local",
                "def",
                line=line,
                var_key=var_key,
                source_ref=source_ref,
                stmt_key=self._make_stmt_key(
                    state.file_path, "def", f"{type_simple} {var_name}", line
                ),
                type_simple=type_simple,
            )
            if declarator.initializer:
                for used in self._extract_vars(
                    declarator.initializer, state.class_fields
                ):
                    use_nid = self._add_var_node(state, used, "use", stmt)
                    self._connect_def_to_use(state, used, use_nid)
                    self._add_edge(state, use_nid, def_nid, "init", var_key=var_key)

    def _analyze_expression(
        self, state: _DFGState, expr: Any, parent_stmt: Any = None
    ) -> None:
        """Analyze an expression.

        Args:
            state(_DFGState): The per-method build state.
            expr(Any): The expression node to analyze.
            parent_stmt(Any): The enclosing statement for source positions.
        """
        if expr is None:
            return
        try:
            if isinstance(expr, Assignment):
                right_nids: list[int] = []
                for var in self._extract_vars(expr.value, state.class_fields):
                    nid = self._add_var_node(state, var, "use", parent_stmt)
                    self._connect_def_to_use(state, var, nid)
                    right_nids.append(nid)
                for var in self._extract_vars(expr.expressionl, state.class_fields):
                    def_nid = self._add_var_node(state, var, "write", parent_stmt)
                    def_var_kind = self._var_kind(var, state.class_fields)
                    def_var_key = self._make_var_key(state, var, def_var_kind)
                    for use_nid in right_nids:
                        self._add_edge(
                            state, use_nid, def_nid, "flow", var_key=def_var_key
                        )
            elif isinstance(expr, MethodInvocation):
                if expr.qualifier:
                    for var in self._extract_vars(expr.qualifier, state.class_fields):
                        nid = self._add_var_node(state, var, "use", parent_stmt)
                        self._connect_def_to_use(state, var, nid)
                if expr.arguments:
                    for arg in expr.arguments:
                        for var in self._extract_vars(arg, state.class_fields):
                            nid = self._add_var_node(state, var, "use", parent_stmt)
                            self._connect_def_to_use(state, var, nid)
            elif isinstance(expr, BinaryOperation):
                for var in self._extract_vars(
                    expr.operandl, state.class_fields
                ) + self._extract_vars(expr.operandr, state.class_fields):
                    nid = self._add_var_node(state, var, "use", parent_stmt)
                    self._connect_def_to_use(state, var, nid)
            elif isinstance(expr, This):
                if hasattr(expr, "selectors") and expr.selectors:
                    for sel in expr.selectors:
                        if isinstance(sel, MethodInvocation):
                            self._analyze_expression(state, sel, parent_stmt)
        except Exception:
            logger.exception(
                f"Graphs(DFG Java): Error analyzing expression {type(expr).__name__}"
            )

    # ------------------------------------------------------------------
    # Variable extraction
    # ------------------------------------------------------------------

    def _extract_vars(
        self, expr: Any, class_fields: dict[str, dict[str, Any]]
    ) -> list[str]:
        """Extract all variable names used in an expression.

        Args:
            expr(Any): The expression node from which to extract variable names.
            class_fields(dict[str, dict[str, Any]]): The owner's field context.

        Returns:
            list[str]: A list of variable names used in the expression.
        """
        variables: list[str] = []
        if expr is None:
            return variables
        try:
            if isinstance(expr, MemberReference):
                if expr.member:
                    if expr.qualifier:
                        qualifier_str = str(expr.qualifier)
                        if qualifier_str == "" or "this" in qualifier_str.lower():
                            if expr.member in class_fields:
                                variables.append(expr.member)
                        else:
                            variables.extend(
                                self._extract_vars(expr.qualifier, class_fields)
                            )
                    else:
                        variables.append(expr.member)
                if hasattr(expr, "selectors") and expr.selectors:
                    for sel in expr.selectors:
                        variables.extend(self._extract_vars(sel, class_fields))
            elif isinstance(expr, This):
                if hasattr(expr, "selectors") and expr.selectors:
                    for sel in expr.selectors:
                        if isinstance(sel, MethodInvocation):
                            if sel.arguments:
                                for arg in sel.arguments:
                                    variables.extend(
                                        self._extract_vars(arg, class_fields)
                                    )
                        elif isinstance(sel, MemberReference):
                            if sel.member and sel.member in class_fields:
                                variables.append(sel.member)
            elif isinstance(expr, MethodInvocation):
                if expr.qualifier:
                    variables.extend(self._extract_vars(expr.qualifier, class_fields))
                if expr.arguments:
                    for arg in expr.arguments:
                        variables.extend(self._extract_vars(arg, class_fields))
            elif isinstance(expr, Assignment):
                variables.extend(self._extract_vars(expr.expressionl, class_fields))
                variables.extend(self._extract_vars(expr.value, class_fields))
            elif isinstance(expr, BinaryOperation):
                variables.extend(self._extract_vars(expr.operandl, class_fields))
                variables.extend(self._extract_vars(expr.operandr, class_fields))
            elif isinstance(expr, ClassCreator):
                if expr.arguments:
                    for arg in expr.arguments:
                        variables.extend(self._extract_vars(arg, class_fields))
            elif isinstance(expr, Cast):
                variables.extend(self._extract_vars(expr.expression, class_fields))
            elif isinstance(expr, TernaryExpression):
                variables.extend(self._extract_vars(expr.condition, class_fields))
                variables.extend(self._extract_vars(expr.if_true, class_fields))
                variables.extend(self._extract_vars(expr.if_false, class_fields))
        except Exception:
            logger.exception(
                f"Graphs(DFG Java): Error extracting variables from {type(expr).__name__}"
            )
        return variables
