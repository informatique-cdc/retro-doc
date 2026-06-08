"""Documentation service.

This module defines the service layer for documentation-related operations.
"""

import json
from typing import Any

from beanie import PydanticObjectId
from langchain.chat_models import init_chat_model
from pymongo.errors import DuplicateKeyError

from app.core.database import mongodb_retry
from app.core.language_enum import Language
from app.docs.config import docs_settings
from app.docs.models import FileDocumentationDocument, MetaRepoDocument
from app.docs.prompts import FILE_DOCUMENTATION_PROMPT, get_language_instructions

chat_model = init_chat_model(
    model=docs_settings.CHAT_NAME,
    model_provider=docs_settings.CHAT_PROVIDER,
    base_url=docs_settings.CHAT_BASE_URL,
    api_key=docs_settings.CHAT_API_KEY.get_secret_value(),
    temperature=docs_settings.CHAT_TEMPERATURE,
)


def create_meta_repo(
    stats: dict[str, int],
) -> str:
    """Create a markdown summary of analysis stats.

    Args:
        stats(dict[str, int]): Dict with keys `total_files`, `ast_success`, `ast_failed`,
            `cfg_success`, `cfg_failed`, `cfg_build_failed`, `dfg_success`, `dfg_failed`,
            `dfg_build_failed`, `doc_success`, `doc_failed`.

    Returns:
        str: The markdown content summarizing the repository analysis results.
    """
    content = (
        "# Résumé de l'analyse\n"
        "\n"
        "| Métrique | Succès | Échec |\n"
        "| ------ | ------: | -----: |\n"
        f"| Fichier  | {stats['total_files']} | — |\n"
        f"| AST    | {stats['ast_success']} | {stats['ast_failed']} |\n"
        f"| CFG    | {stats['cfg_success']} | {stats['cfg_failed']} ({stats['cfg_build_failed']}) |\n"
        f"| DFG    | {stats['dfg_success']} | {stats['dfg_failed']} ({stats['dfg_build_failed']}) |\n"
        f"| Documentation    | {stats['doc_success']} | {stats['doc_failed']} |\n"
    )

    return content


async def generate_documentation_file(
    file_path: str,
    source_code: str,
    ast: dict[str, Any] | None,
    cfgs: list[dict[str, Any]] | None,
    dfgs: list[dict[str, Any]] | None,
    language: Language,
) -> str:
    """Generate file-level documentation using an LLM.

    Builds a prompt from the source code and its AST/CFG/DFG
    representations, invokes the chat model, and returns the
    resulting documentation.

    Args:
        file_path(str): Relative path of the source file.
        source_code(str): The raw source code content.
        ast(dict[str, Any] | None): Parsed AST dict, or None on
            parse failure.
        cfgs(list[dict[str, Any]] | None): List of CFG dicts,
            or None on build failure.
        dfgs(list[dict[str, Any]] | None): List of DFG dicts,
            or None on build failure.
        language(Language): The programming language of the file.

    Returns:
        str: The generated documentation
            content for the file.
    """
    try:
        ast_json = json.dumps(ast, indent=2)[: docs_settings.PROMPT_MAX_GRAPH_CHARS]
    except (TypeError, ValueError):
        ast_json = "N/A"

    try:
        cfg_json = json.dumps(cfgs, indent=2)[: docs_settings.PROMPT_MAX_GRAPH_CHARS]
    except (TypeError, ValueError):
        cfg_json = "N/A"

    try:
        dfg_json = json.dumps(dfgs, indent=2)[: docs_settings.PROMPT_MAX_GRAPH_CHARS]
    except (TypeError, ValueError):
        dfg_json = "N/A"

    messages = FILE_DOCUMENTATION_PROMPT.invoke(
        {
            "file_path": file_path,
            "source_code": source_code[: docs_settings.PROMPT_MAX_SOURCE_CHARS],
            "ast_json": ast_json,
            "cfg_json": cfg_json,
            "dfg_json": dfg_json,
            "language": language.value,
            "language_instructions": get_language_instructions(language),
        }
    )

    response = await chat_model.ainvoke(messages)

    return response.text


async def persist_meta_repo(repo_id: PydanticObjectId, content: str) -> None:
    """Persist a meta repo document in the database.

    Args:
        repo_id(PydanticObjectId): The repository identifier.
        content(str): The markdown content summarizing the repository analysis results.
    """
    meta_doc = MetaRepoDocument(
        repo_id=repo_id,
        content=content,
    )
    try:
        await mongodb_retry(meta_doc.insert)
    except DuplicateKeyError:
        pass


async def persist_documentation(
    repo_id: PydanticObjectId,
    file_id: PydanticObjectId | None,
    content: str,
) -> None:
    """Persist a documentation document in the database.

    Args:
        repo_id(PydanticObjectId): The repository identifier.
        file_id(PydanticObjectId | None): The file identifier.
        content(str): The generated documentation content for the file.
    """
    doc = FileDocumentationDocument(
        repo_id=repo_id,
        file_id=file_id,
        content=content,
    )
    try:
        await mongodb_retry(doc.insert)
    except DuplicateKeyError:
        pass
