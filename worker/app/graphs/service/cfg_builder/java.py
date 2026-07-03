"""Java CFG building service.

This module builds Control Flow Graphs for Java methods by parsing
source code with javalang and walking method bodies.
"""

import hashlib
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
    Literal,
    LocalVariableDeclaration,
    MemberReference,
    MethodInvocation,
    ReturnStatement,
    StatementExpression,
    SwitchStatement,
    TernaryExpression,
    This,
    TryStatement,
    WhileStatement,
)
from loguru import logger

from app.graphs.service.ast_parser import JavaASTParserService
from app.graphs.service.cfg_builder.base import CFGBuilderService


class JavaCFGBuilderService(CFGBuilderService):
    """Build control flow graphs for Java methods."""

    def build(self, source_code: str, file_path: str) -> list[dict[str, Any]]:
        """Build CFGs for all methods in a Java file.

        Args:
            source_code(str): The raw Java source code.
            file_path(str): The file path for metadata/logging purposes.

        Returns:
            list[dict[str, Any]]: List of dicts, one per method,
                each containing the CFG representation with nodes,
                edges, and metrics.

        Raises:
            Exception: If parsing fails.
        """
        tree = javalang.parse.parse(source_code)

        # Reuse the AST parser to obtain canonical, overload-safe method keys
        # (method:<ownerFQN>#<name>(<paramFQNs>):<retFQN>) without duplicating its
        # type-resolution logic. Keys are mapped back to methods by position, since
        # the AST parser iterates the same parsed tree in the same order.
        key_map = self._build_key_map(source_code, file_path)

        results: list[dict[str, Any]] = []

        for _, node in tree.filter(ClassDeclaration):
            if node.methods:
                owner_keys = key_map.get(node.name, [])
                for idx, method in enumerate(node.methods):
                    scope = owner_keys[idx] if idx < len(owner_keys) else ""
                    if not scope:
                        logger.warning(
                            f"Graphs(CFG Java): CFG skipped, no canonical key for "
                            f"'{node.name}.{method.name}' in '{file_path}'"
                        )
                        continue
                    cfg = self._build_method_cfg(method, scope, file_path)
                    results.append(cfg)

        for _, node in tree.filter(InterfaceDeclaration):
            if node.methods:
                owner_keys = key_map.get(node.name, [])
                for idx, method in enumerate(node.methods):
                    if method.body:
                        scope = owner_keys[idx] if idx < len(owner_keys) else ""
                        if not scope:
                            logger.warning(
                                f"Graphs(CFG Java): CFG skipped, no canonical key for "
                                f"'{node.name}.{method.name}' in '{file_path}'"
                            )
                            continue
                        cfg = self._build_method_cfg(method, scope, file_path)
                        results.append(cfg)

        return results

    def _build_key_map(self, source_code: str, file_path: str) -> dict[str, list[str]]:
        """Build a map of owner name to its ordered list of canonical method keys.

        Delegates to `JavaASTParserService.extract_method_keys` so the CFG `scope`
        matches the AST's canonical `method:<ownerFQN>#<name>(<paramFQNs>):<retFQN>`
        key. Java's graph builders share the Java AST parser's canonical key as the
        single identity authority. Method keys are listed in declaration order so
        they can be mapped back to javalang method nodes by position (overload-safe).

        Args:
            source_code(str): The raw Java source code.
            file_path(str): The file path used for AST metadata.

        Returns:
            dict[str, list[str]]: Mapping of class/interface name to the ordered
                list of canonical method keys.
        """
        try:
            return JavaASTParserService().extract_method_keys(source_code)
        except Exception:
            logger.exception(
                f"Graphs(CFG Java): AST key extraction failed for '{file_path}'"
            )
            return {}

    # ------------------------------------------------------------------
    # CFG construction
    # ------------------------------------------------------------------

    def _build_method_cfg(
        self, java_method: Any, scope: str, file_path: str
    ) -> dict[str, Any]:
        """Build a CFG dict for a single method.

        Args:
            java_method(Any): The javalang MethodDeclaration node.
            scope(str): The canonical method key used as the document scope.
            file_path(str): The file path used for node source_ref/stmt_key.

        Returns:
            dict[str, Any]: A dict containing the CFG representation with
                nodes, edges, and metrics.
        """
        method_name = java_method.name

        graph = nx.DiGraph()
        counter = [0]  # mutable counter

        entry = self._add_node(graph, counter, "entry", "ENTRY")
        exit_node = self._add_node(graph, counter, "exit", "EXIT")

        if java_method.body:
            last_nodes = self._process_block(
                graph, counter, java_method.body, entry, exit_node, file_path
            )
            for nid in last_nodes:
                if nid != exit_node:
                    self._add_edge(graph, nid, exit_node)
        else:
            self._add_edge(graph, entry, exit_node)

        # Compute cyclomatic complexity
        edges = graph.number_of_edges()
        nodes = graph.number_of_nodes()
        components = nx.number_weakly_connected_components(graph) if nodes > 0 else 1
        complexity = max(1, edges - nodes + 2 * components) if nodes > 0 else 1

        return {
            "method_name": method_name,
            "scope": scope,
            "nodes": [{"id": nid, **graph.nodes[nid]} for nid in graph.nodes()],
            "edges": [
                {
                    "from": u,
                    "to": v,
                    "label": graph.edges[u, v].get("label", ""),
                }
                for u, v in graph.edges()
            ],
            "metrics": {
                "nodes_count": nodes,
                "edges_count": edges,
                "cyclomatic_complexity": complexity,
            },
        }

    def _add_node(
        self,
        graph: nx.DiGraph,
        counter: list[int],
        node_type: str,
        label: str = "",
        source_ref: dict[str, Any] | None = None,
        stmt_key: str | None = None,
        normalized_text: str | None = None,
    ) -> int:
        """Add a node to the graph with a unique ID and return the ID.

        Args:
            graph(nx.DiGraph): The graph being built.
            counter(list[int]): Mutable node ID counter.
            node_type(str): The type of the node (e.g., "statement", "branch").
            label(str): A human-readable label for the node.
            source_ref(dict[str, Any] | None): Source reference metadata.
            stmt_key(str | None): Canonical statement key (matches the AST).
            normalized_text(str | None): Normalized text for textual joins.

        Returns:
            int: The unique ID of the added node.
        """
        nid = counter[0]
        counter[0] += 1
        metadata: dict[str, Any] = {}
        if source_ref is not None:
            metadata["source_ref"] = source_ref
        if stmt_key is not None:
            metadata["stmt_key"] = stmt_key
        if normalized_text is not None:
            metadata["normalized_text"] = normalized_text
        graph.add_node(nid, id=nid, type=node_type, label=label, metadata=metadata)
        return nid

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

    def _compute_stmt_key(self, file_path: str, line: int, kind: str, text: str) -> str:
        """Compute a stable statement key (matches the AST parser).

        Args:
            file_path(str): The file path.
            line(int): The line number.
            kind(str): The statement kind (lowercase).
            text(str): The statement text.

        Returns:
            str: A stable key of the form `stmt:<path>:<line>:<kind>:<hash>`.
        """
        text_hash = hashlib.sha1(
            text.encode("utf-8", errors="replace"), usedforsecurity=False
        ).hexdigest()[:8]
        return f"stmt:{file_path}:{line}:{kind}:{text_hash}"

    def _make_source_ref(self, file_path: str, line: int) -> dict[str, Any]:
        """Build a source_ref dictionary for a single-line node.

        Args:
            file_path(str): The file path.
            line(int): The source line number.

        Returns:
            dict[str, Any]: A source reference dictionary.
        """
        return {"file": file_path, "line_start": line, "line_end": line}

    def _add_statement_node(
        self,
        graph: nx.DiGraph,
        counter: list[int],
        node: Any,
        node_type: str,
        label: str,
        kind: str,
        file_path: str,
    ) -> int:
        """Add a statement/branch node carrying source_ref and stmt_key metadata.

        Args:
            graph(nx.DiGraph): The graph being built.
            counter(list[int]): Mutable node ID counter.
            node(Any): The javalang node used for the source position.
            node_type(str): The CFG node type (e.g., "statement", "branch").
            label(str): A human-readable label (also used as normalized_text).
            kind(str): The statement kind for the stmt_key (e.g., "if", "stmt").
            file_path(str): The file path for source_ref/stmt_key.

        Returns:
            int: The unique ID of the added node.
        """
        line = self._get_node_line(node)
        source_ref = (
            self._make_source_ref(file_path, line) if line is not None else None
        )
        stmt_key = (
            self._compute_stmt_key(file_path, line, kind, label)
            if line is not None
            else None
        )
        return self._add_node(
            graph,
            counter,
            node_type,
            label,
            source_ref=source_ref,
            stmt_key=stmt_key,
            normalized_text=label,
        )

    def _add_edge(self, graph: nx.DiGraph, src: int, dst: int, label: str = "") -> None:
        """Add a directed edge to the graph with an optional label.

        Args:
            graph(nx.DiGraph): The graph being built.
            src(int): The source node ID.
            dst(int): The destination node ID.
            label(str): A human-readable label for the edge (e.g., "true", "false").
        """
        graph.add_edge(src, dst, label=label)

    # ------------------------------------------------------------------
    # Statement processing
    # ------------------------------------------------------------------

    def _process_block(
        self,
        graph: nx.DiGraph,
        counter: list[int],
        statements: list[Any],
        entry_id: int,
        exit_id: int,
        file_path: str,
    ) -> list[int]:
        """Process a block of statements, connecting them in sequence and
        returning the last nodes in the block.

        Args:
            graph(nx.DiGraph): The graph being built.
            counter(list[int]): Mutable node ID counter.
            statements(list[Any]): List of javalang statements in the block.
            entry_id(int): The node ID to connect the block's entry to.
            exit_id(int): The node ID to connect the block's exit to.
            file_path(str): The file path for node source_ref/stmt_key.

        Returns:
            list[int]: List of node IDs that are the last nodes in the block.
        """
        if not statements:
            return [entry_id]

        current = [entry_id]
        for stmt in statements:
            current = self._process_statement(
                graph, counter, stmt, current, exit_id, file_path
            )
        return current

    def _process_statement(
        self,
        graph: nx.DiGraph,
        counter: list[int],
        stmt: Any,
        entry_nodes: list[int],
        exit_id: int,
        file_path: str,
    ) -> list[int]:
        """Process a single statement, adding nodes and edges to the graph as
        needed, and returning the last nodes after processing.

        Args:
            graph(nx.DiGraph): The graph being built.
            counter(list[int]): Mutable node ID counter.
            stmt(Any): The javalang statement to process.
            entry_nodes(list[int]): List of node IDs to connect the statement's
                entry to.
            exit_id(int): The node ID to connect the statement's exit to.
            file_path(str): The file path for node source_ref/stmt_key.

        Returns:
            list[int]: List of node IDs that are the last nodes after processing
                the statement.
        """
        if isinstance(stmt, IfStatement):
            return self._process_if(
                graph, counter, stmt, entry_nodes, exit_id, file_path
            )
        if isinstance(stmt, WhileStatement):
            return self._process_while(
                graph, counter, stmt, entry_nodes, exit_id, file_path
            )
        if isinstance(stmt, ForStatement):
            return self._process_for(
                graph, counter, stmt, entry_nodes, exit_id, file_path
            )
        if isinstance(stmt, TryStatement):
            return self._process_try(
                graph, counter, stmt, entry_nodes, exit_id, file_path
            )
        if isinstance(stmt, SwitchStatement):
            return self._process_switch(
                graph, counter, stmt, entry_nodes, exit_id, file_path
            )
        if isinstance(stmt, ReturnStatement):
            return self._process_return(
                graph, counter, stmt, entry_nodes, exit_id, file_path
            )
        if isinstance(stmt, BlockStatement):
            return self._process_block(
                graph,
                counter,
                stmt.statements,
                entry_nodes[0] if entry_nodes else exit_id,
                exit_id,
                file_path,
            )
        return self._process_simple(graph, counter, stmt, entry_nodes, file_path)

    def _process_if(
        self,
        graph: nx.DiGraph,
        counter: list[int],
        stmt: Any,
        entry_nodes: list[int],
        exit_id: int,
        file_path: str,
    ) -> list[int]:
        """Process an if statement, creating a branch node and connecting then and
        else branches appropriately.

        Args:
            graph(nx.DiGraph): The graph being built.
            counter(list[int]): Mutable node ID counter.
            stmt(Any): The javalang IfStatement to process.
            entry_nodes(list[int]): List of node IDs to connect the statement's
                entry to.
            exit_id(int): The node ID to connect the statement's exit to.
            file_path(str): The file path for node source_ref/stmt_key.

        Returns:
            list[int]: List of node IDs that are the last nodes after processing
                the if statement.
        """
        cond = self._add_statement_node(
            graph,
            counter,
            stmt,
            "branch",
            f"if ({self._label(stmt.condition)})",
            "if",
            file_path,
        )
        for eid in entry_nodes:
            self._add_edge(graph, eid, cond)

        then_last = self._process_statement(
            graph, counter, stmt.then_statement, [cond], exit_id, file_path
        )
        if then_last:
            self._add_edge(graph, cond, then_last[0], "true")

        if stmt.else_statement:
            else_last = self._process_statement(
                graph, counter, stmt.else_statement, [cond], exit_id, file_path
            )
            if else_last:
                self._add_edge(graph, cond, else_last[0], "false")
            return then_last + else_last

        return then_last + [cond]

    def _process_while(
        self,
        graph: nx.DiGraph,
        counter: list[int],
        stmt: Any,
        entry_nodes: list[int],
        exit_id: int,
        file_path: str,
    ) -> list[int]:
        """Process a while statement, creating a branch node and connecting the body
        back to the condition.

        Args:
            graph(nx.DiGraph): The graph being built.
            counter(list[int]): Mutable node ID counter.
            stmt(Any): The javalang WhileStatement to process.
            entry_nodes(list[int]): List of node IDs to connect the statement's
                entry to.
            exit_id(int): The node ID to connect the statement's exit to.
            file_path(str): The file path for node source_ref/stmt_key.

        Returns:
            list[int]: List of node IDs that are the last nodes after processing
                the while statement.
        """
        cond = self._add_statement_node(
            graph,
            counter,
            stmt,
            "branch",
            f"while ({self._label(stmt.condition)})",
            "while",
            file_path,
        )
        for eid in entry_nodes:
            self._add_edge(graph, eid, cond)

        body_last = self._process_statement(
            graph, counter, stmt.body, [cond], exit_id, file_path
        )
        if body_last:
            self._add_edge(graph, cond, body_last[0], "true")
        for nid in body_last:
            self._add_edge(graph, nid, cond, "loop")

        return [cond]

    def _process_for(
        self,
        graph: nx.DiGraph,
        counter: list[int],
        stmt: Any,
        entry_nodes: list[int],
        exit_id: int,
        file_path: str,
    ) -> list[int]:
        """Process a for statement, creating a branch node and connecting
        the body back to the condition.

        Args:
            graph(nx.DiGraph): The graph being built.
            counter(list[int]): Mutable node ID counter.
            stmt(Any): The javalang ForStatement to process.
            entry_nodes(list[int]): List of node IDs to connect the statement's
                entry to.
            exit_id(int): The node ID to connect the statement's exit to.
            file_path(str): The file path for node source_ref/stmt_key.

        Returns:
            list[int]: List of node IDs that are the last nodes after processing
                the for statement.
        """
        ctrl_label = (
            self._label(stmt.control)
            if hasattr(stmt, "control") and stmt.control
            else "..."
        )
        cond = self._add_statement_node(
            graph, counter, stmt, "branch", f"for ({ctrl_label})", "for", file_path
        )
        for eid in entry_nodes:
            self._add_edge(graph, eid, cond)

        body_last = self._process_statement(
            graph, counter, stmt.body, [cond], exit_id, file_path
        )
        if body_last:
            self._add_edge(graph, cond, body_last[0], "true")
        for nid in body_last:
            self._add_edge(graph, nid, cond, "loop")

        return [cond]

    def _process_try(
        self,
        graph: nx.DiGraph,
        counter: list[int],
        stmt: Any,
        entry_nodes: list[int],
        exit_id: int,
        file_path: str,
    ) -> list[int]:
        """Process a try statement, connecting the try block, catch blocks,
        and finally block.

        Args:
            graph(nx.DiGraph): The graph being built.
            counter(list[int]): Mutable node ID counter.
            stmt(Any): The javalang TryStatement to process.
            entry_nodes(list[int]): List of node IDs to connect the statement's
                entry to.
            exit_id(int): The node ID to connect the statement's exit to.
            file_path(str): The file path for node source_ref/stmt_key.

        Returns:
            list[int]: List of node IDs that are the last nodes after processing
                the try statement.
        """
        try_last = self._process_block(
            graph,
            counter,
            stmt.block,
            entry_nodes[0] if entry_nodes else exit_id,
            exit_id,
            file_path,
        )
        last_nodes = list(try_last)

        if stmt.catches:
            for catch in stmt.catches:
                catch_type = "Exception"
                if hasattr(catch.parameter, "types") and catch.parameter.types:
                    catch_type = " | ".join(str(t) for t in catch.parameter.types)
                elif hasattr(catch.parameter, "type") and hasattr(
                    catch.parameter.type, "name"
                ):
                    catch_type = catch.parameter.type.name
                catch_node = self._add_statement_node(
                    graph,
                    counter,
                    catch,
                    "statement",
                    f"catch ({catch_type})",
                    "catch",
                    file_path,
                )
                for eid in entry_nodes:
                    self._add_edge(graph, eid, catch_node, "exception")
                catch_last = self._process_block(
                    graph, counter, catch.block, catch_node, exit_id, file_path
                )
                last_nodes.extend(catch_last)

        if stmt.finally_block:
            finally_node = self._add_statement_node(
                graph, counter, stmt, "statement", "finally", "finally", file_path
            )
            for nid in last_nodes:
                self._add_edge(graph, nid, finally_node)
            return self._process_block(
                graph,
                counter,
                stmt.finally_block,
                finally_node,
                exit_id,
                file_path,
            )

        return last_nodes

    def _process_switch(
        self,
        graph: nx.DiGraph,
        counter: list[int],
        stmt: Any,
        entry_nodes: list[int],
        exit_id: int,
        file_path: str,
    ) -> list[int]:
        """Process a switch statement, creating a branch node and connecting case
        blocks appropriately.

        Args:
            graph(nx.DiGraph): The graph being built.
            counter(list[int]): Mutable node ID counter.
            stmt(Any): The javalang SwitchStatement to process.
            entry_nodes(list[int]): List of node IDs to connect the statement's
                entry to.
            exit_id(int): The node ID to connect the statement's exit to.
            file_path(str): The file path for node source_ref/stmt_key.

        Returns:
            list[int]: List of node IDs that are the last nodes after processing
                the switch statement.
        """
        switch = self._add_statement_node(
            graph,
            counter,
            stmt,
            "branch",
            f"switch ({self._label(stmt.expression)})",
            "switch",
            file_path,
        )
        for eid in entry_nodes:
            self._add_edge(graph, eid, switch)

        last_nodes: list[int] = []
        for case in stmt.cases:
            if case.case:
                value = self._label(case.case[0])
                case_label = f"case {value}"
                edge_label = f"case:{value}"
            else:
                case_label = "default"
                edge_label = "default"
            case_node = self._add_statement_node(
                graph, counter, case, "statement", case_label, "case", file_path
            )
            self._add_edge(graph, switch, case_node, edge_label)
            if case.statements:
                case_last = self._process_block(
                    graph,
                    counter,
                    case.statements,
                    case_node,
                    exit_id,
                    file_path,
                )
                last_nodes.extend(case_last)
            else:
                last_nodes.append(case_node)

        return last_nodes if last_nodes else [switch]

    def _process_return(
        self,
        graph: nx.DiGraph,
        counter: list[int],
        stmt: Any,
        entry_nodes: list[int],
        exit_id: int,
        file_path: str,
    ) -> list[int]:
        """Process a return statement, creating a return node and connecting it to exit.

        Args:
            graph(nx.DiGraph): The graph being built.
            counter(list[int]): Mutable node ID counter.
            stmt(Any): The javalang ReturnStatement to process.
            entry_nodes(list[int]): List of node IDs to connect the statement's
                entry to.
            exit_id(int): The node ID to connect the statement's exit to.
            file_path(str): The file path for node source_ref/stmt_key.

        Returns:
            list[int]: List of node IDs that are the last nodes after processing
                the return statement (empty since it goes to exit).
        """
        label = "return"
        if stmt.expression:
            label = f"return {self._label(stmt.expression)}"
        ret = self._add_statement_node(
            graph, counter, stmt, "statement", label, "return", file_path
        )
        for eid in entry_nodes:
            self._add_edge(graph, eid, ret)
        self._add_edge(graph, ret, exit_id)
        return []

    def _process_simple(
        self,
        graph: nx.DiGraph,
        counter: list[int],
        stmt: Any,
        entry_nodes: list[int],
        file_path: str,
    ) -> list[int]:
        node = self._add_statement_node(
            graph, counter, stmt, "statement", self._label(stmt), "stmt", file_path
        )
        for eid in entry_nodes:
            self._add_edge(graph, eid, node)
        return [node]

    # ------------------------------------------------------------------
    # Label generation
    # ------------------------------------------------------------------

    def _label(self, stmt: Any, max_length: int = 200) -> str:
        """Get a readable label for a statement or expression.

        Args:
            stmt(Any): The javalang statement or expression to label.
            max_length(int): Maximum length of the label string.

        Returns:
            str: A human-readable label for the statement or expression.
        """
        if stmt is None:
            return ""
        try:
            if isinstance(stmt, MemberReference):
                result = ""
                if stmt.qualifier:
                    result = f"{self._label(stmt.qualifier)}.{stmt.member}"
                else:
                    result = stmt.member
                if hasattr(stmt, "selectors") and stmt.selectors:
                    for sel in stmt.selectors:
                        if isinstance(sel, MethodInvocation):
                            args = self._label_args(sel.arguments)
                            result += f".{sel.member}({args})"
                        elif isinstance(sel, MemberReference):
                            result += f".{sel.member}"
                return result

            if isinstance(stmt, MethodInvocation):
                qualifier = f"{self._label(stmt.qualifier)}." if stmt.qualifier else ""
                args = self._label_args(stmt.arguments)
                return f"{qualifier}{stmt.member}({args})"

            if isinstance(stmt, Assignment):
                left = self._label(stmt.expressionl)
                right = self._label(stmt.value)
                return f"{left} = {right}"

            if isinstance(stmt, BinaryOperation):
                left = self._label(stmt.operandl)
                right = self._label(stmt.operandr)
                return f"{left} {stmt.operator} {right}"

            if isinstance(stmt, Literal):
                val = stmt.value
                if val is None or (isinstance(val, str) and val.lower() == "null"):
                    return "null"
                if isinstance(val, str) and val.lower() in ("true", "false", "null"):
                    return val.lower()
                if isinstance(val, str):
                    return f'"{val}"'
                return str(val)

            if isinstance(stmt, This):
                result = "this"
                if hasattr(stmt, "selectors") and stmt.selectors:
                    for sel in stmt.selectors:
                        if isinstance(sel, MethodInvocation):
                            args = self._label_args(sel.arguments)
                            result += f".{sel.member}({args})"
                        elif isinstance(sel, MemberReference):
                            result += f".{sel.member}"
                        else:
                            result += f".{self._label(sel)}"
                return result

            if isinstance(stmt, StatementExpression):
                return self._label(stmt.expression)

            if isinstance(stmt, LocalVariableDeclaration):
                type_name = (
                    stmt.type.name if hasattr(stmt.type, "name") else str(stmt.type)
                )
                parts = []
                for decl in stmt.declarators:
                    if decl.initializer:
                        parts.append(f"{decl.name} = {self._label(decl.initializer)}")
                    else:
                        parts.append(decl.name)
                return f"{type_name} {', '.join(parts)}"

            if isinstance(stmt, ClassCreator):
                type_name = (
                    stmt.type.name if hasattr(stmt.type, "name") else str(stmt.type)
                )
                args = self._label_args(stmt.arguments)
                return f"new {type_name}({args})"

            if isinstance(stmt, Cast):
                type_name = (
                    stmt.type.name if hasattr(stmt.type, "name") else str(stmt.type)
                )
                return f"({type_name}) {self._label(stmt.expression)}"

            if isinstance(stmt, TernaryExpression):
                cond = self._label(stmt.condition)
                t = self._label(stmt.if_true)
                f = self._label(stmt.if_false)
                return f"{cond} ? {t} : {f}"

            if hasattr(stmt, "__class__"):
                return stmt.__class__.__name__  # type: ignore

            result = str(stmt)
            if len(result) > max_length:
                return result[: max_length - 3] + "..."
            return result

        except Exception:
            logger.exception(
                f"Graphs(CFG Java): Error generating label for {type(stmt)}"
            )
            return type(stmt).__name__

    def _label_args(self, arguments: list[Any] | None) -> str:
        """Get a readable label for a list of method arguments.

        Args:
            arguments(list[Any] | None): List of javalang expressions representing
                method arguments.

        Returns:
            str: A human-readable label for the method arguments, joined by commas.
        """
        if not arguments:
            return ""
        return ", ".join(self._label(arg) for arg in arguments)
