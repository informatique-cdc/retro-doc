"""Graph registry service.

This module maps supported languages to their graph service implementations
(source preparer, AST parser, CFG builder, DFG builder).
"""

from typing import NamedTuple

from app.core.language import Language
from app.graphs.service.ast_parser import ASTParserService, JavaASTParserService
from app.graphs.service.cfg_builder import CFGBuilderService, JavaCFGBuilderService
from app.graphs.service.dfg_builder import DFGBuilderService, JavaDFGBuilderService
from app.graphs.service.preparer import JavaSourcePreparerService, SourcePreparerService


class GraphServices(NamedTuple):
    """The graph services registered for one language.

    The three builders stay independent: each can source its own representation.
    `preparer` is the optional shared step for languages whose builders happen to
    derive from the same artifact, so they pay for it once.
    """

    preparer: SourcePreparerService
    ast_parser: ASTParserService
    cfg_builder: CFGBuilderService
    dfg_builder: DFGBuilderService


_graph_services: dict[Language, GraphServices] = {
    Language.JAVA: GraphServices(
        preparer=JavaSourcePreparerService(),
        ast_parser=JavaASTParserService(),
        cfg_builder=JavaCFGBuilderService(),
        dfg_builder=JavaDFGBuilderService(),
    ),
}


def get_graph_services(language: Language) -> GraphServices:
    """Get the graph services for a language.

    Args:
        language(Language): The programming language.

    Returns:
        GraphServices: The preparer, AST parser, CFG builder and DFG builder.

    Raises:
        ValueError: If the language is not supported.
    """
    if language not in _graph_services:
        raise ValueError(f"Unsupported language: {language}")
    return _graph_services[language]
