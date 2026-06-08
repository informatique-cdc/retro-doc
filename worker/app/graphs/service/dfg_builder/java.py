"""Java DFG building service.

This module builds Data Flow Graphs for Java methods by parsing
source code with javalang and tracking variable definitions and uses.
"""

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

from app.graphs.service.dfg_builder.base import DFGBuilderService


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

        results: list[dict[str, Any]] = []

        for _, node in tree.filter(ClassDeclaration):
            class_fields = self._extract_class_fields(node)
            if node.methods:
                for method in node.methods:
                    dfg = self._build_method_dfg(method, node.name, class_fields)
                    results.append(dfg)

        for _, node in tree.filter(InterfaceDeclaration):
            if node.methods:
                for method in node.methods:
                    if method.body:
                        dfg = self._build_method_dfg(method, node.name, {})
                        results.append(dfg)

        return results

    # ------------------------------------------------------------------
    # Tree helpers
    # ------------------------------------------------------------------

    def _extract_class_fields(
        self, class_node: ClassDeclaration
    ) -> dict[str, dict[str, Any]]:
        """Extract class fields into a dict for easy lookup.

        Args:
            class_node (ClassDeclaration): The class declaration
                node from which to extract fields.

        Returns:
            dict[str, dict[str, Any]]: A dictionary mapping field names to
                their metadata (currently just the name).
        """
        fields: dict[str, dict[str, Any]] = {}
        if class_node.fields:
            for field_decl in class_node.fields:
                for declarator in field_decl.declarators:
                    fields[declarator.name] = {"name": declarator.name}
        return fields

    # ------------------------------------------------------------------
    # DFG construction helpers
    # ------------------------------------------------------------------

    def _add_node(
        self,
        graph: nx.DiGraph,
        counter: list[int],
        defs: dict[str, list[int]],
        uses: dict[str, list[int]],
        last_def: dict[str, int],
        variable: str,
        node_type: str,
        operation: str,
    ) -> int:
        """Add a node to the graph and update defs/uses tracking.

        Args:
            graph (nx.DiGraph): The graph to which the node will be added.
            counter (list[int]): A single-item list used to generate unique node IDs.
            defs (dict[str, list[int]]): A mapping of variable names to lists of definition node IDs.
            uses (dict[str, list[int]]): A mapping of variable names to lists of use node IDs.
            last_def (dict[str, int]): A mapping of variable names to the most recent definition node ID.
            variable (str): The name of the variable associated with this node.
            node_type (str): The type of the variable (e.g., "local", "field", "param").
            operation (str): The operation type ("def", "use", "write", "read", "init").

        Returns:
            int: The ID of the newly added node.
        """
        nid = counter[0]
        counter[0] += 1
        graph.add_node(
            nid,
            id=nid,
            variable=variable,
            type=node_type,
            operation=operation,
        )
        if operation in ("def", "write", "init"):
            defs.setdefault(variable, []).append(nid)
            last_def[variable] = nid
        elif operation in ("use", "read"):
            uses.setdefault(variable, []).append(nid)
        return nid

    def _add_edge(
        self, graph: nx.DiGraph, src: int, dst: int, edge_type: str = "flow"
    ) -> None:
        """Add an edge to the graph if it doesn't already exist.

        Args:
            graph (nx.DiGraph): The graph to which the edge will be added.
            src (int): The source node ID.
            dst (int): The destination node ID.
            edge_type (str): The type of the edge (default is "flow").
        """
        if not graph.has_edge(src, dst):
            graph.add_edge(src, dst, type=edge_type)

    def _connect_def_to_use(
        self,
        graph: nx.DiGraph,
        last_def: dict[str, int],
        variable: str,
        use_nid: int,
    ) -> None:
        """Connect the most recent definition of a variable to a new use node.

        Args:
            graph (nx.DiGraph): The graph to which the edge will be added.
            last_def (dict[str, int]): A mapping of variable names to the most recent definition node ID.
            variable (str): The name of the variable being used.
            use_nid (int): The node ID of the use node that should be connected to the definition.
        """
        if variable in last_def:
            self._add_edge(graph, last_def[variable], use_nid, "def-use")

    # ------------------------------------------------------------------
    # DFG construction
    # ------------------------------------------------------------------

    def _build_method_dfg(
        self,
        java_method: Any,
        class_name: str,
        class_fields: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Build a DFG dict for a single method.

        Args:
            java_method (Any): The method node from which to build the DFG.
            class_name (str): The name of the class containing the method, used for scoping.
            class_fields (dict[str, dict[str, Any]]): A dictionary of class fields for
                variable type resolution.

        Returns:
            dict[str, Any]: A dictionary containing the method name, scope,
                list of nodes, list of edges, defs mapping, and uses mapping.
        """
        method_name = java_method.name
        scope = f"{class_name}.{method_name}"

        graph = nx.DiGraph()
        counter = [0]
        defs: dict[str, list[int]] = {}
        uses: dict[str, list[int]] = {}
        last_def: dict[str, int] = {}

        # Add parameter nodes as implicit definitions
        for param in java_method.parameters or []:
            param_name = param.name
            nid = self._add_node(
                graph,
                counter,
                defs,
                uses,
                last_def,
                param_name,
                "param",
                "def",
            )
            last_def[param_name] = nid

        # Analyze method body
        if java_method.body:
            self._analyze_block(
                java_method.body,
                class_fields,
                graph,
                counter,
                defs,
                uses,
                last_def,
            )

        return {
            "method_name": method_name,
            "scope": scope,
            "nodes": [{"id": nid, **graph.nodes[nid]} for nid in graph.nodes()],
            "edges": [
                {
                    "from": u,
                    "to": v,
                    "type": graph.edges[u, v].get("type", ""),
                }
                for u, v in graph.edges()
            ],
            "defs": defs,
            "uses": uses,
        }

    # ------------------------------------------------------------------
    # Statement analysis
    # ------------------------------------------------------------------

    def _analyze_block(
        self,
        statements: list[Any],
        class_fields: dict[str, dict[str, Any]],
        graph: nx.DiGraph,
        counter: list[int],
        defs: dict[str, list[int]],
        uses: dict[str, list[int]],
        last_def: dict[str, int],
    ) -> None:
        """Analyze a block of statements and update the graph, defs, and uses.

        Args:
            statements (list[Any]): A list of statement nodes to analyze.
            class_fields (dict[str, dict[str, Any]]): A dictionary of class fields for
                variable type resolution.
            graph (nx.DiGraph): The graph being constructed.
            counter (list[int]): A single-item list used to generate unique node IDs.
            defs (dict[str, list[int]]): A mapping of variable names to lists of definition
                node IDs.
            uses (dict[str, list[int]]): A mapping of variable names to lists of use
                node IDs.
            last_def (dict[str, int]): A mapping of variable names to the most recent
                definition node ID.
        """
        if not statements:
            return
        for stmt in statements:
            self._analyze_statement(
                stmt,
                class_fields,
                graph,
                counter,
                defs,
                uses,
                last_def,
            )

    def _analyze_statement(
        self,
        stmt: Any,
        class_fields: dict[str, dict[str, Any]],
        graph: nx.DiGraph,
        counter: list[int],
        defs: dict[str, list[int]],
        uses: dict[str, list[int]],
        last_def: dict[str, int],
    ) -> None:
        """Analyze a single statement and update the graph, defs, and uses.

        Args:
            stmt (Any): The statement node to analyze.
            class_fields (dict[str, dict[str, Any]]): A dictionary of class fields for
                variable type resolution.
            graph (nx.DiGraph): The graph being constructed.
            counter (list[int]): A single-item list used to generate unique node IDs.
            defs (dict[str, list[int]]): A mapping of variable names to lists of definition
                node IDs.
            uses (dict[str, list[int]]): A mapping of variable names to lists of use
                node IDs.
            last_def (dict[str, int]): A mapping of variable names to the most recent
                definition node ID.
        """
        try:
            if isinstance(stmt, LocalVariableDeclaration):
                self._analyze_local_var_decl(
                    stmt,
                    class_fields,
                    graph,
                    counter,
                    defs,
                    uses,
                    last_def,
                )
            elif isinstance(stmt, StatementExpression):
                self._analyze_expression(
                    stmt.expression,
                    class_fields,
                    graph,
                    counter,
                    defs,
                    uses,
                    last_def,
                )
            elif isinstance(stmt, IfStatement):
                for var in self._extract_vars(stmt.condition, class_fields):
                    nid = self._add_node(
                        graph,
                        counter,
                        defs,
                        uses,
                        last_def,
                        var,
                        self._var_type(var, class_fields),
                        "use",
                    )
                    self._connect_def_to_use(graph, last_def, var, nid)
                self._analyze_statement(
                    stmt.then_statement,
                    class_fields,
                    graph,
                    counter,
                    defs,
                    uses,
                    last_def,
                )
                if stmt.else_statement:
                    self._analyze_statement(
                        stmt.else_statement,
                        class_fields,
                        graph,
                        counter,
                        defs,
                        uses,
                        last_def,
                    )
            elif isinstance(stmt, WhileStatement):
                for var in self._extract_vars(stmt.condition, class_fields):
                    nid = self._add_node(
                        graph,
                        counter,
                        defs,
                        uses,
                        last_def,
                        var,
                        self._var_type(var, class_fields),
                        "use",
                    )
                    self._connect_def_to_use(graph, last_def, var, nid)
                self._analyze_statement(
                    stmt.body,
                    class_fields,
                    graph,
                    counter,
                    defs,
                    uses,
                    last_def,
                )
            elif isinstance(stmt, ForStatement):
                if hasattr(stmt, "control") and stmt.control:
                    if hasattr(stmt.control, "init"):
                        self._analyze_expression(
                            stmt.control.init,
                            class_fields,
                            graph,
                            counter,
                            defs,
                            uses,
                            last_def,
                        )
                    if hasattr(stmt.control, "condition"):
                        self._analyze_expression(
                            stmt.control.condition,
                            class_fields,
                            graph,
                            counter,
                            defs,
                            uses,
                            last_def,
                        )
                self._analyze_statement(
                    stmt.body,
                    class_fields,
                    graph,
                    counter,
                    defs,
                    uses,
                    last_def,
                )
            elif isinstance(stmt, ReturnStatement):
                if stmt.expression:
                    for var in self._extract_vars(stmt.expression, class_fields):
                        nid = self._add_node(
                            graph,
                            counter,
                            defs,
                            uses,
                            last_def,
                            var,
                            self._var_type(var, class_fields),
                            "use",
                        )
                        self._connect_def_to_use(graph, last_def, var, nid)
            elif isinstance(stmt, BlockStatement):
                self._analyze_block(
                    stmt.statements,
                    class_fields,
                    graph,
                    counter,
                    defs,
                    uses,
                    last_def,
                )
            elif isinstance(stmt, TryStatement):
                self._analyze_block(
                    stmt.block,
                    class_fields,
                    graph,
                    counter,
                    defs,
                    uses,
                    last_def,
                )
                if stmt.catches:
                    for catch in stmt.catches:
                        self._analyze_block(
                            catch.block,
                            class_fields,
                            graph,
                            counter,
                            defs,
                            uses,
                            last_def,
                        )
                if stmt.finally_block:
                    self._analyze_block(
                        stmt.finally_block,
                        class_fields,
                        graph,
                        counter,
                        defs,
                        uses,
                        last_def,
                    )
        except Exception as e:
            logger.debug(f"Error analyzing statement {type(stmt).__name__}: {e}")

    def _analyze_local_var_decl(
        self,
        stmt: LocalVariableDeclaration,
        class_fields: dict[str, dict[str, Any]],
        graph: nx.DiGraph,
        counter: list[int],
        defs: dict[str, list[int]],
        uses: dict[str, list[int]],
        last_def: dict[str, int],
    ) -> None:
        """Analyze a local variable declaration statement, adding definition nodes and edges
        for initializers.

        Args:
            stmt (LocalVariableDeclaration): The local variable declaration statement to analyze.
            class_fields (dict[str, dict[str, Any]]): A dictionary of class fields for
                variable type resolution.
            graph (nx.DiGraph): The graph being constructed.
            counter (list[int]): A single-item list used to generate unique node IDs.
            defs (dict[str, list[int]]): A mapping of variable names to lists of definition
                node IDs.
            uses (dict[str, list[int]]): A mapping of variable names to lists of use
                node IDs.
            last_def (dict[str, int]): A mapping of variable names to the most recent
                definition node ID.
        """
        for declarator in stmt.declarators:
            def_nid = self._add_node(
                graph,
                counter,
                defs,
                uses,
                last_def,
                declarator.name,
                "local",
                "def",
            )
            if declarator.initializer:
                for var in self._extract_vars(declarator.initializer, class_fields):
                    use_nid = self._add_node(
                        graph,
                        counter,
                        defs,
                        uses,
                        last_def,
                        var,
                        self._var_type(var, class_fields),
                        "use",
                    )
                    self._connect_def_to_use(graph, last_def, var, use_nid)
                    self._add_edge(graph, use_nid, def_nid, "init")

    def _analyze_expression(
        self,
        expr: Any,
        class_fields: dict[str, dict[str, Any]],
        graph: nx.DiGraph,
        counter: list[int],
        defs: dict[str, list[int]],
        uses: dict[str, list[int]],
        last_def: dict[str, int],
    ) -> None:
        """Analyze an expression and update the graph, defs, and uses.

        Args:
            expr (Any): The expression node to analyze.
            class_fields (dict[str, dict[str, Any]]): A dictionary of class fields for
                variable type resolution.
            graph (nx.DiGraph): The graph being constructed.
            counter (list[int]): A single-item list used to generate unique node IDs.
            defs (dict[str, list[int]]): A mapping of variable names to lists of definition
                node IDs.
            uses (dict[str, list[int]]): A mapping of variable names to lists of use
                node IDs.
            last_def (dict[str, int]): A mapping of variable names to the most recent
                definition node ID.
        """
        if expr is None:
            return
        try:
            if isinstance(expr, Assignment):
                right_vars = self._extract_vars(expr.value, class_fields)
                right_nids: list[int] = []
                for var in right_vars:
                    nid = self._add_node(
                        graph,
                        counter,
                        defs,
                        uses,
                        last_def,
                        var,
                        self._var_type(var, class_fields),
                        "use",
                    )
                    self._connect_def_to_use(graph, last_def, var, nid)
                    right_nids.append(nid)
                for var in self._extract_vars(expr.expressionl, class_fields):
                    def_nid = self._add_node(
                        graph,
                        counter,
                        defs,
                        uses,
                        last_def,
                        var,
                        self._var_type(var, class_fields),
                        "write",
                    )
                    for use_nid in right_nids:
                        self._add_edge(graph, use_nid, def_nid, "flow")
            elif isinstance(expr, MethodInvocation):
                if expr.qualifier:
                    for var in self._extract_vars(expr.qualifier, class_fields):
                        nid = self._add_node(
                            graph,
                            counter,
                            defs,
                            uses,
                            last_def,
                            var,
                            self._var_type(var, class_fields),
                            "use",
                        )
                        self._connect_def_to_use(graph, last_def, var, nid)
                if expr.arguments:
                    for arg in expr.arguments:
                        for var in self._extract_vars(arg, class_fields):
                            nid = self._add_node(
                                graph,
                                counter,
                                defs,
                                uses,
                                last_def,
                                var,
                                self._var_type(var, class_fields),
                                "use",
                            )
                            self._connect_def_to_use(graph, last_def, var, nid)
            elif isinstance(expr, BinaryOperation):
                for var in self._extract_vars(
                    expr.operandl, class_fields
                ) + self._extract_vars(expr.operandr, class_fields):
                    nid = self._add_node(
                        graph,
                        counter,
                        defs,
                        uses,
                        last_def,
                        var,
                        self._var_type(var, class_fields),
                        "use",
                    )
                    self._connect_def_to_use(graph, last_def, var, nid)
            elif isinstance(expr, This):
                if hasattr(expr, "selectors") and expr.selectors:
                    for sel in expr.selectors:
                        if isinstance(sel, MethodInvocation):
                            self._analyze_expression(
                                sel,
                                class_fields,
                                graph,
                                counter,
                                defs,
                                uses,
                                last_def,
                            )
        except Exception as e:
            logger.debug(f"Error analyzing expression {type(expr).__name__}: {e}")

    # ------------------------------------------------------------------
    # Variable extraction
    # ------------------------------------------------------------------

    def _extract_vars(
        self, expr: Any, class_fields: dict[str, dict[str, Any]]
    ) -> list[str]:
        """Extract all variable names used in an expression.

        Args:
            expr (Any): The expression node from which to extract variable names.
            class_fields (dict[str, dict[str, Any]]): A dictionary of class fields for
                variable type resolution.

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
        except Exception as e:
            logger.debug(f"Error extracting variables from {type(expr).__name__}: {e}")
        return variables

    def _var_type(self, var_name: str, class_fields: dict[str, dict[str, Any]]) -> str:
        """Determine the type of a variable (field vs local) based on class fields.

        Args:
            var_name (str): The name of the variable.
            class_fields (dict[str, dict[str, Any]]): A dictionary of class fields for
                variable type resolution.
        """
        return "field" if var_name in class_fields else "local"
