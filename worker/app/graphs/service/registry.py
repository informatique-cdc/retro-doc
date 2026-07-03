"""Graph registry service.

This module maps supported languages to their graph service implementations
(AST parser, CFG builder, DFG builder).
"""

from app.core.language import Language
from app.graphs.service.ast_parser import ASTParserService, JavaASTParserService
from app.graphs.service.cfg_builder import CFGBuilderService, JavaCFGBuilderService
from app.graphs.service.dfg_builder import DFGBuilderService, JavaDFGBuilderService

_graph_services: dict[
    Language,
    tuple[ASTParserService, CFGBuilderService, DFGBuilderService],
] = {
    Language.JAVA: (
        JavaASTParserService(),
        JavaCFGBuilderService(),
        JavaDFGBuilderService(),
    ),
}


def get_graph_services(
    language: Language,
) -> tuple[ASTParserService, CFGBuilderService, DFGBuilderService]:
    """Get the AST parser, CFG builder, and DFG builder for a language.

    Args:
        language(Language): The programming language.

    Returns:
        tuple: (ASTParserService, CFGBuilderService, DFGBuilderService)

    Raises:
        ValueError: If the language is not supported.
    """
    if language not in _graph_services:
        raise ValueError(f"Unsupported language: {language}")
    return _graph_services[language]
