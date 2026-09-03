"""Chat agent tools.

This module defines the tools available to the LangGraph chat agent. Beware docstrings
for these tools will be directly visible to the agent, so they should be clear and concise.
"""

import fnmatch
import json
import re
from typing import Literal

from beanie import PydanticObjectId
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime

from app.chat.config import chat_settings
from app.chat.models import ChatMessageDocument
from app.chat.vectorstore import get_vectorstore
from app.core.blob_storage import get_container_client
from app.docs.models import FileDocumentationDocument, RepoMetaDocument
from app.graphs.models import ASTDocument, CFGDocument, DFGDocument
from app.repos.models import FileDocument, RepoDocument


def _glob_to_regex(pattern: str) -> str:
    """Convert a glob pattern to a MongoDB-compatible regex string.

    Args:
        pattern(str): A glob pattern (e.g. `**/*.py`, `src/*.ts`).

    Returns:
        str: A regex string suitable for MongoDB `$regex`.
    """
    result: list[str] = []
    i = 0
    pattern_len = len(pattern)
    while i < pattern_len:
        c = pattern[i]
        if c == "*" and i + 1 < pattern_len and pattern[i + 1] == "*":
            result.append(".*")
            i += 2
            if i < pattern_len and pattern[i] == "/":
                i += 1
        elif c == "*":
            result.append("[^/]*")
            i += 1
        elif c == "?":
            result.append("[^/]")
            i += 1
        elif c in r"\.+^${}()|[]":
            result.append("\\" + c)
            i += 1
        else:
            result.append(c)
            i += 1
    return "^" + "".join(result) + "$"


def _truncate_content(content: str, max_length: int) -> str:
    """Truncate content to *max_length* with a marker.

    Args:
        content(str): The content to potentially truncate.
        max_length(int): The maximum allowed length.

    Returns:
        str: The original content if its length is within the threshold, or a
            truncated version with a marker indicating truncation and total
            length if it exceeds the threshold.
    """
    if len(content) <= max_length:
        return content
    return content[:max_length] + f"\n\n[...truncated, {len(content)} chars total]"


@tool
async def retrieve_messages(
    runtime: ToolRuntime,
    query: str | None = None,
    limit: int = 10,
    start: int = 1,
    role: Literal["human", "ai"] | None = None,
    full_content: bool = False,
) -> str:
    """Retrieve messages from the current conversation history.

    Use role="human" when the user asks about "my messages", role="ai" for
    "your replies". Omit role only for the conversation as a whole.
    start accepts negative values to count from the end (-1 = last message).
    full_content=True disables truncation of long messages.

    Examples:
        - "My first 2 messages" → role="human", limit=2, start=1
        - "Your last reply" → role="ai", limit=1, start=-1
        - "Messages about auth" → query="auth"
        - "3rd message" → start=3, limit=1
    """
    thread_id = PydanticObjectId(runtime.config["configurable"]["thread_id"])
    limit = max(1, min(limit, chat_settings.RETRIEVE_MESSAGES_MAX_RESULTS))

    filters: dict[str, object] = {"thread_id": thread_id}
    if role is not None:
        filters["role"] = role
    if query is not None:
        filters["content"] = {"$regex": re.escape(query), "$options": "i"}

    messages = await ChatMessageDocument.find(filters).sort("+created_at").to_list()

    total = len(messages)
    if total == 0:
        return "No messages found in this conversation."

    # Positional slicing
    if start >= 1:
        sliced = messages[start - 1 : start - 1 + limit]
        first_pos = start
    else:
        # Negative start: count from end
        slice_start = max(total + start, 0)
        sliced = messages[slice_start : slice_start + limit]
        first_pos = slice_start + 1

    if not sliced:
        return f"No messages at the requested position (total messages: {total})."

    header = f"Retrieved {len(sliced)} of {total} total messages (positions are absolute across all turns)"
    if role is not None:
        header += f" (filtered by role: {role})"
    header += ":\n"

    lines: list[str] = [header]
    for i, msg in enumerate(sliced):
        pos = first_pos + i
        content = (
            msg.content
            if full_content
            else _truncate_content(
                msg.content, chat_settings.RETRIEVE_MESSAGES_MAX_CONTENT_LENGTH
            )
        )
        lines.append(f"#{pos} [{msg.role}]: {content}")

    return "\n".join(lines)


@tool(response_format="content_and_artifact")
async def repo_search_docs(
    runtime: ToolRuntime,
    query: str,
) -> tuple[str, list[dict[str, str]]]:
    """Search documentation of the repository under analysis using hybrid search (keyword + semantic).

    Use this when the user asks about the codebase: structure, functionality,
    implementation details, or any repo-specific question. Returns the most
    relevant documentation chunks from the analyzed repository.
    """
    repo_id = runtime.config["configurable"]["repo_id"]
    vector_store = get_vectorstore()

    results = await vector_store.asimilarity_search(
        query=query,
        k=chat_settings.REPO_SEARCH_DOCS_TOP_K,
        search_type="semantic_hybrid",
        filters=f"repo_id eq '{repo_id}'",
    )

    if not results:
        return "No documentation found for this repository.", []

    file_paths: list[str] = []
    sections: list[str] = []
    seen_paths: set[str] = set()
    for doc in results:
        file_path = doc.metadata.get("file_path", "unknown")
        sections.append(f"### {file_path}\n{doc.page_content}")
        if file_path != "unknown" and file_path not in seen_paths:
            file_paths.append(file_path)
            seen_paths.add(file_path)

    # Batch-resolve FileDocument IDs
    file_docs = await FileDocument.find(
        {"repo_id": PydanticObjectId(repo_id), "path": {"$in": file_paths}}
    ).to_list()
    path_to_id: dict[str, PydanticObjectId] = {fd.path: fd.id for fd in file_docs}

    # Build file refs artifact
    file_refs: list[dict[str, str]] = [
        {"path": path, "file_id": str(file_id)}
        for path in file_paths
        if (file_id := path_to_id.get(path)) is not None
    ]

    return "\n\n---\n\n".join(sections), file_refs


@tool(response_format="content_and_artifact")
async def repo_glob(
    runtime: ToolRuntime,
    directory: str = "",
    pattern: str | None = None,
    offset: int = 0,
) -> tuple[str, list[dict[str, str]]]:
    """List files and directories in the repository under analysis.

    Browse directories or search with glob patterns in the analyzed
    repository. Use `directory` to navigate the file tree, `pattern`
    to filter results. Results are paginated, use `offset` to fetch
    the next page.

    Examples:
        - List repo root → directory=""
        - Browse a folder → directory="src/utils"
        - Python files in folder → directory="src", pattern="*.py"
        - All Java files → pattern="**/*.java"
        - All test files → pattern="**/test_*"
        - Only folders named "service" → pattern="**/service/"
        - All folders recursively → pattern="**/"
        - Next page → pattern="**/*.java", offset=100
    """
    repo_id = runtime.config["configurable"]["repo_id"]
    repo_oid = PydanticObjectId(repo_id)
    prefix = directory.strip("/") + "/" if directory.strip("/") else ""
    offset = max(0, offset)
    page_size = chat_settings.REPO_GLOB_MAX_RESULTS
    docs: list[FileDocument] = []
    dirs_only = False
    match_pattern = None
    is_recursive = False

    if pattern is not None:
        dirs_only = pattern.endswith("/")
        match_pattern = pattern.rstrip("/")
        is_recursive = "**" in match_pattern

    if is_recursive:
        # Recursive browsing mode (matches both files and directories)
        full_pattern = prefix + match_pattern  # type: ignore[operator]
        file_regex = _glob_to_regex(full_pattern)
        dir_regex = _glob_to_regex(full_pattern + "/**")

        combined_regex = f"({file_regex})|({dir_regex})"
        filters: dict[str, object] = {
            "repo_id": repo_oid,
            "path": {"$regex": combined_regex},
        }
        docs = await FileDocument.find(filters).sort("+path").to_list()

        # Collect exact file matches + infer directory matches
        file_re = re.compile(file_regex)
        matched_files: list[str] = []
        matched_dirs: set[str] = set()
        for doc in docs:
            if not dirs_only and file_re.match(doc.path):
                matched_files.append(doc.path)
            # Extract all ancestor directories and keep those matching the
            # original pattern to surface directory-only results.
            parts = doc.path.split("/")
            for depth in range(1, len(parts)):
                ancestor = "/".join(parts[:depth])
                if file_re.match(ancestor):
                    matched_dirs.add(ancestor + "/")

        entries = sorted(matched_dirs) + sorted(matched_files)
    else:
        # Directory browsing mode
        filters = {"repo_id": repo_oid}
        if prefix:
            filters["path"] = {"$regex": f"^{re.escape(prefix)}"}

        docs = await FileDocument.find(filters).to_list()

        # Extract immediate children
        dirs: set[str] = set()
        files: list[str] = []
        for doc in docs:
            relative = doc.path[len(prefix) :]
            slash_idx = relative.find("/")
            if slash_idx != -1:
                dirs.add(relative[: slash_idx + 1])
            else:
                files.append(relative)

        # Apply optional non-recursive pattern filter
        if match_pattern is not None:
            dirs = {d for d in dirs if fnmatch.fnmatch(d.rstrip("/"), match_pattern)}
            files = [f for f in files if fnmatch.fnmatch(f, match_pattern)]

        if dirs_only:
            entries = sorted(dirs)
        else:
            entries = sorted(dirs) + sorted(files)

    total_entries = len(entries)
    if total_entries == 0:
        label = prefix.rstrip("/") if prefix else "repository root"
        if pattern:
            return f"No entries matching '{pattern}' in {label}.", []
        return f"No files found in {label}.", []

    page = entries[offset : offset + page_size]
    if not page:
        return f"No entries at offset {offset} (total: {total_entries}).", []

    show_start = offset + 1
    show_end = offset + len(page)
    pagination = f"showing {show_start}-{show_end} of {total_entries}"

    if is_recursive:
        entry_type = "entr" + ("y" if total_entries == 1 else "ies")
        header = (
            f"Found {total_entries} {entry_type} matching {pattern} ({pagination}):"
        )
    else:
        label = prefix or "/"
        header = f"Contents of {label} ({pagination}):"

    lines: list[str] = [header]
    for entry in page:
        lines.append(f"  {entry}")

    # Build file refs artifact (directories excluded — they have no FileDocument)
    path_to_id: dict[str, PydanticObjectId] = {doc.path: doc.id for doc in docs}
    file_refs: list[dict[str, str]] = []
    for entry in page:
        if entry.endswith("/"):
            continue
        full_path = entry if is_recursive else prefix + entry
        file_id = path_to_id.get(full_path)
        if file_id is not None:
            file_refs.append({"path": full_path, "file_id": str(file_id)})

    return "\n".join(lines), file_refs


@tool(response_format="content_and_artifact")
async def repo_read_file(
    runtime: ToolRuntime,
    path: str,
    full_content: bool = False,
) -> tuple[str, list[dict[str, str]]]:
    """Read the source code of a file in the repository under analysis.

    Returns the raw file content from the analyzed repository. Provide
    the exact file path as shown in repo_glob or repo_search_docs results.
    Long files are truncated by default. Use full_content=True to
    retrieve the complete file without truncation.

    Examples:
        - "Show me src/main.py" → path="src/main.py"
        - "Read the config file" → path="config/settings.yaml"
    """
    repo_id = runtime.config["configurable"]["repo_id"]
    repo_oid = PydanticObjectId(repo_id)

    file_doc = await FileDocument.find_one(
        FileDocument.repo_id == repo_oid,
        FileDocument.path == path,
    )
    if file_doc is None:
        return f"File not found: {path}", []

    repo = await RepoDocument.find_one(RepoDocument.id == repo_oid)
    if repo is None:
        return "Repository not found.", []

    container_client = get_container_client()
    blob_path = f"{repo.blob_path}/{file_doc.path}"
    downloader = await container_client.download_blob(blob_path)
    raw = await downloader.readall()
    content = raw.decode(errors="replace")

    if not full_content:
        content = _truncate_content(
            content, chat_settings.REPO_READ_FILE_MAX_CONTENT_LENGTH
        )

    file_refs: list[dict[str, str]] = [{"path": path, "file_id": str(file_doc.id)}]
    return f"### {path}\n```\n{content}\n```", file_refs


@tool(response_format="content_and_artifact")
async def repo_read_file_documentation(
    runtime: ToolRuntime,
    path: str,
) -> tuple[str, list[dict[str, str]]]:
    """Read the generated documentation for a file in the repository under analysis.

    Returns the full documentation content produced by the analysis
    pipeline for the analyzed repository. Provide the exact file path
    as shown in repo_glob or repo_search_docs results.

    Examples:
        - "Documentation for src/main.py" → path="src/main.py"
    """
    repo_id = runtime.config["configurable"]["repo_id"]
    repo_oid = PydanticObjectId(repo_id)

    file_doc = await FileDocument.find_one(
        FileDocument.repo_id == repo_oid,
        FileDocument.path == path,
    )
    if file_doc is None:
        return f"File not found: {path}", []

    documentation = await FileDocumentationDocument.find_one(
        FileDocumentationDocument.repo_id == repo_oid,
        FileDocumentationDocument.file_id == file_doc.id,
    )
    if documentation is None:
        return f"No documentation found for {path}.", []

    file_refs: list[dict[str, str]] = [{"path": path, "file_id": str(file_doc.id)}]
    return f"### {path}\n{documentation.content}", file_refs


@tool(response_format="content_and_artifact")
async def repo_read_file_graph(
    runtime: ToolRuntime,
    path: str,
    graph_type: Literal["ast", "cfg", "dfg"],
    full_content: bool = False,
) -> tuple[str, list[dict[str, str]]]:
    """Read a code analysis graph for a file in the repository under analysis.

    Returns the graph data as JSON from the analyzed repository.
    Choose the graph type:
    - ast: Abstract Syntax Tree (full file structure)
    - cfg: Control Flow Graphs (one per scope/function)
    - dfg: Data Flow Graphs (one per scope/function)
    full_content=True disables truncation of large graphs.

    Examples:
        - "AST of src/main.py" → path="src/main.py", graph_type="ast"
        - "Control flow of utils.py" → path="utils.py", graph_type="cfg"
    """
    repo_id = runtime.config["configurable"]["repo_id"]
    repo_oid = PydanticObjectId(repo_id)

    file_doc = await FileDocument.find_one(
        FileDocument.repo_id == repo_oid,
        FileDocument.path == path,
    )
    if file_doc is None:
        return f"File not found: {path}", []

    if graph_type == "ast":
        ast_doc = await ASTDocument.find_one(
            ASTDocument.repo_id == repo_oid,
            ASTDocument.file_id == file_doc.id,
        )
        if ast_doc is None:
            return f"No AST graph found for {path}.", []
        data = json.dumps(ast_doc.content, indent=2)
    elif graph_type == "cfg":
        cfg_docs = await CFGDocument.find(
            CFGDocument.repo_id == repo_oid,
            CFGDocument.file_id == file_doc.id,
        ).to_list()
        if not cfg_docs:
            return f"No CFG graphs found for {path}.", []
        data = json.dumps(
            [{"scope": d.scope, "content": d.content} for d in cfg_docs],
            indent=2,
        )
    else:
        dfg_docs = await DFGDocument.find(
            DFGDocument.repo_id == repo_oid,
            DFGDocument.file_id == file_doc.id,
        ).to_list()
        if not dfg_docs:
            return f"No DFG graphs found for {path}.", []
        data = json.dumps(
            [{"scope": d.scope, "content": d.content} for d in dfg_docs],
            indent=2,
        )

    if not full_content:
        data = _truncate_content(
            data, chat_settings.REPO_READ_FILE_GRAPH_MAX_CONTENT_LENGTH
        )

    file_refs: list[dict[str, str]] = [{"path": path, "file_id": str(file_doc.id)}]
    return f"### {path} ({graph_type.upper()})\n```json\n{data}\n```", file_refs


@tool
async def repo_read_metadata(
    runtime: ToolRuntime,
) -> str:
    """Read the repository-level metadata and summary of the repository under analysis.

    Returns the high-level description of the analyzed repository
    produced by the analysis pipeline. Use this to get an overview of
    the project before diving into specific files.
    """
    repo_id = runtime.config["configurable"]["repo_id"]
    repo_oid = PydanticObjectId(repo_id)

    meta = await RepoMetaDocument.find_one(
        RepoMetaDocument.repo_id == repo_oid,
    )
    if meta is None:
        return "No metadata found for this repository."

    return meta.content
