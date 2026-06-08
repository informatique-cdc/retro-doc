"""Unit tests for chat tools.

This module tests the chat tools.
"""

import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from app.chat.config import chat_settings
from app.chat.tools import (
    _glob_to_regex,
    _truncate_content,
    repo_glob,
    repo_read_file,
    repo_read_file_documentation,
    repo_read_file_graph,
    repo_read_metadata,
    repo_search_docs,
    retrieve_messages,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runtime(
    thread_id: str = "000000000000000000000010",
    repo_id: str = "000000000000000000000020",
) -> MagicMock:
    """Build a mock `ToolRuntime` with configurable thread/repo IDs."""
    runtime = MagicMock()
    runtime.config = {"configurable": {"thread_id": thread_id, "repo_id": repo_id}}
    return runtime


def _mock_file_doc(path: str, file_id: str = "aaaaaaaaaaaaaaaaaaaaaaaa") -> MagicMock:
    """Build a mock ``FileDocument`` with *path* and *id*."""
    doc = MagicMock()
    doc.path = path
    doc.id = PydanticObjectId(file_id)
    return doc


def _mock_msg(role: str, content: str) -> MagicMock:
    """Build a mock `ChatMessageDocument`."""
    msg = MagicMock()
    msg.role = role
    msg.content = content
    return msg


def _patch_find_files(docs: list[MagicMock], *, sorted_query: bool = False) -> Any:
    """Patch `FileDocument.find(...).to_list()` or `.sort(...).to_list()`.

    When *sorted_query* is True, the chain includes `.sort(...)` (recursive mode).
    """
    if sorted_query:
        mock_to_list = AsyncMock(return_value=docs)
        mock_sort = MagicMock()
        mock_sort.to_list = mock_to_list
        mock_find = MagicMock()
        mock_find.sort = MagicMock(return_value=mock_sort)
    else:
        mock_to_list = AsyncMock(return_value=docs)
        mock_find = MagicMock()
        mock_find.to_list = mock_to_list

    return patch("app.chat.tools.FileDocument.find", return_value=mock_find)


def _patch_find_messages(messages: list[MagicMock]) -> Any:
    """Patch `ChatMessageDocument.find(...).sort(...).to_list()` to return *messages*."""
    mock_sort = MagicMock()
    mock_sort.to_list = AsyncMock(return_value=messages)
    mock_find = MagicMock()
    mock_find.sort = MagicMock(return_value=mock_sort)
    return patch("app.chat.tools.ChatMessageDocument.find", return_value=mock_find)


def _patch_find_one(target: str, return_value: Any) -> Any:
    """Patch a ``Model.find_one(...)`` call on *target* to return *return_value*."""
    return patch(target, new_callable=AsyncMock, return_value=return_value)


def _mock_repo_doc(blob_path: str = "repos/abc123/code") -> MagicMock:
    """Build a mock ``RepoDocument`` with *blob_path*."""
    doc = MagicMock()
    doc.blob_path = blob_path
    return doc


def _mock_blob_download(content: str) -> Any:
    """Patch ``get_container_client().download_blob()`` to return *content*."""
    mock_downloader = AsyncMock()
    mock_downloader.readall = AsyncMock(return_value=content.encode())
    mock_container = MagicMock()
    mock_container.download_blob = AsyncMock(return_value=mock_downloader)
    return patch("app.chat.tools.get_container_client", return_value=mock_container)


def _mock_doc_content(content: str) -> MagicMock:
    """Build a mock document with a ``content`` attribute."""
    doc = MagicMock()
    doc.content = content
    return doc


def _mock_graph_doc(content: dict[str, Any], scope: str | None = None) -> MagicMock:
    """Build a mock graph document with ``content`` and optional ``scope``."""
    doc = MagicMock()
    doc.content = content
    doc.scope = scope
    return doc


def _patch_find_graph_docs(target: str, docs: list[MagicMock]) -> Any:
    """Patch a ``Model.find(...).to_list()`` call to return *docs*."""
    mock_to_list = AsyncMock(return_value=docs)
    mock_find = MagicMock()
    mock_find.to_list = mock_to_list
    return patch(target, return_value=mock_find)


# ---------------------------------------------------------------------------
# _glob_to_regex
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "should_match", "should_not_match"),
    [
        ("**/*.py", ["src/main.py", "a/b/c.py"], ["main.txt"]),
        ("**/test_*", ["test_foo", "a/b/test_bar"], []),
        ("?.py", ["a.py"], ["ab.py", "/.py"]),
        ("file.name+v2", ["file.name+v2"], ["fileXnameXv2"]),
        ("src/*.ts", ["src/app.ts"], ["src/sub/app.ts"]),
    ],
    ids=[
        "double_star_py",
        "double_star_prefix",
        "question_mark",
        "special_chars",
        "single_star",
    ],
)
def test_glob_to_regex_matching(
    pattern: str,
    should_match: list[str],
    should_not_match: list[str],
) -> None:
    """_glob_to_regex produces correct regex for glob patterns."""
    regex = _glob_to_regex(pattern)
    compiled = re.compile(regex)

    for path in should_match:
        assert compiled.match(path), f"{pattern!r} should match {path!r}"
    for path in should_not_match:
        assert not compiled.match(path), f"{pattern!r} should not match {path!r}"


# ---------------------------------------------------------------------------
# retrieve_messages
# ---------------------------------------------------------------------------

_retrieve = retrieve_messages.coroutine  # type: ignore[attr-defined]


async def test_retrieve_messages_empty() -> None:
    """Returns 'No messages found' when DB is empty."""
    with _patch_find_messages([]):
        result = await _retrieve(runtime=_make_runtime())

    assert result == "No messages found in this conversation."


async def test_retrieve_messages_filters_by_query() -> None:
    """Passes regex query filter to the DB query."""
    msgs = [_mock_msg("human", "auth stuff")]

    with _patch_find_messages(msgs) as mock_find:
        await _retrieve(runtime=_make_runtime(), query="auth")

    call_args = mock_find.call_args[0][0]
    assert "$regex" in call_args["content"]


async def test_retrieve_messages_filters_by_role() -> None:
    """Passes role filter to the DB query."""
    msgs = [_mock_msg("human", "Hello")]

    with _patch_find_messages(msgs) as mock_find:
        result = await _retrieve(runtime=_make_runtime(), role="human")

    call_args = mock_find.call_args[0][0]
    assert call_args["role"] == "human"
    assert "(filtered by role: human)" in result


async def test_retrieve_messages_full_content() -> None:
    """full_content=True disables truncation."""
    max_len = chat_settings.RETRIEVE_MESSAGES_MAX_CONTENT_LENGTH
    long_content = "a" * (max_len + 100)
    msgs = [_mock_msg("human", long_content)]

    with _patch_find_messages(msgs):
        result = await _retrieve(runtime=_make_runtime(), full_content=True)

    assert "truncated" not in result
    assert long_content in result


async def test_retrieve_messages_negative_start() -> None:
    """Negative start counts from the end."""
    msgs = [_mock_msg("human", "First"), _mock_msg("ai", "Second")]

    with _patch_find_messages(msgs):
        result = await _retrieve(runtime=_make_runtime(), start=-1, limit=1)

    assert "#2 [ai]: Second" in result
    assert "Retrieved 1 of 2" in result


async def test_retrieve_messages_out_of_range_position() -> None:
    """Returns position error when start is beyond available messages."""
    msgs = [_mock_msg("human", "Only one")]

    with _patch_find_messages(msgs):
        result = await _retrieve(runtime=_make_runtime(), start=5)

    assert "No messages at the requested position" in result


async def test_retrieve_messages_returns_formatted_output() -> None:
    """Returns messages with positions and a header."""
    msgs = [_mock_msg("human", "Hello"), _mock_msg("ai", "Hi there!")]

    with _patch_find_messages(msgs):
        result = await _retrieve(runtime=_make_runtime())

    assert "Retrieved 2 of 2" in result
    assert "#1 [human]: Hello" in result
    assert "#2 [ai]: Hi there!" in result


# ---------------------------------------------------------------------------
# search_repo_docs
# ---------------------------------------------------------------------------

_search = repo_search_docs.coroutine  # type: ignore[attr-defined]


async def test_search_repo_docs_empty_results() -> None:
    """Returns 'No documentation found' when VectorStore returns nothing."""
    mock_vs = AsyncMock()
    mock_vs.asimilarity_search = AsyncMock(return_value=[])

    with patch("app.chat.tools.get_vectorstore", return_value=mock_vs):
        content, artifact = await _search(runtime=_make_runtime(), query="nonexistent")

    assert content == "No documentation found for this repository."
    assert artifact == []


async def test_search_repo_docs_returns_formatted_sections() -> None:
    """Returns formatted sections with file paths and content."""
    mock_doc = MagicMock()
    mock_doc.metadata = {"file_path": "src/main.py"}
    mock_doc.page_content = "Main entry point."

    mock_vs = AsyncMock()
    mock_vs.asimilarity_search = AsyncMock(return_value=[mock_doc])

    file_doc = _mock_file_doc("src/main.py", file_id="bbbbbbbbbbbbbbbbbbbbbbbb")
    mock_find = MagicMock()
    mock_find.to_list = AsyncMock(return_value=[file_doc])

    with (
        patch("app.chat.tools.get_vectorstore", return_value=mock_vs),
        patch("app.chat.tools.FileDocument.find", return_value=mock_find),
    ):
        content, artifact = await _search(runtime=_make_runtime(), query="main entry")

    assert "### src/main.py" in content
    assert "Main entry point." in content
    assert len(artifact) == 1
    assert artifact[0]["path"] == "src/main.py"
    assert artifact[0]["file_id"] == "bbbbbbbbbbbbbbbbbbbbbbbb"


# ---------------------------------------------------------------------------
# glob
# ---------------------------------------------------------------------------

_glob = repo_glob.coroutine  # type: ignore[attr-defined]


async def test_glob_directory_listing() -> None:
    """Lists immediate children (dirs + files) when no pattern is given."""
    docs = [
        _mock_file_doc("src/main.py"),
        _mock_file_doc("src/utils/helper.py"),
        _mock_file_doc("README.md"),
    ]

    with _patch_find_files(docs):
        content, artifact = await _glob(runtime=_make_runtime())

    assert "Contents of /" in content
    assert "src/" in content
    assert "README.md" in content
    # Only files (not directories) appear in the artifact
    artifact_paths = {ref["path"] for ref in artifact}
    assert "README.md" in artifact_paths


async def test_glob_dirs_only_pattern() -> None:
    """Pattern ending with `/` returns only directories."""
    docs = [
        _mock_file_doc("src/utils/helper.py"),
        _mock_file_doc("src/models/user.py"),
    ]

    with _patch_find_files(docs, sorted_query=True):
        content, artifact = await _glob(runtime=_make_runtime(), pattern="**/")

    assert "src/" in content
    assert "helper.py" not in content
    assert artifact == []


async def test_glob_empty_results_no_pattern() -> None:
    """Returns 'No files found' when directory is empty."""
    with _patch_find_files([]):
        content, artifact = await _glob(runtime=_make_runtime())

    assert "No files found in repository root." in content
    assert artifact == []


async def test_glob_empty_results_with_pattern() -> None:
    """Returns 'No entries matching' when pattern matches nothing."""
    with _patch_find_files([], sorted_query=True):
        content, artifact = await _glob(runtime=_make_runtime(), pattern="**/*.rs")

    assert "No entries matching" in content
    assert artifact == []


async def test_glob_non_recursive_pattern() -> None:
    """Filters immediate children with a non-recursive pattern."""
    docs = [
        _mock_file_doc("src/app.py"),
        _mock_file_doc("src/app.ts"),
        _mock_file_doc("src/utils/helper.py"),
    ]

    with _patch_find_files(docs):
        content, artifact = await _glob(
            runtime=_make_runtime(), directory="src", pattern="*.py"
        )

    assert "app.py" in content
    assert "app.ts" not in content
    artifact_paths = {ref["path"] for ref in artifact}
    assert "src/app.py" in artifact_paths


async def test_glob_offset_out_of_range() -> None:
    """Returns message when offset is beyond total entries."""
    docs = [_mock_file_doc("file.py")]

    with _patch_find_files(docs):
        content, artifact = await _glob(runtime=_make_runtime(), offset=100)

    assert "No entries at offset 100" in content
    assert artifact == []


async def test_glob_pagination_offset() -> None:
    """Offset paginates through results."""
    paths = [f"file{i}.py" for i in range(chat_settings.REPO_GLOB_MAX_RESULTS + 5)]
    docs = [_mock_file_doc(p) for p in paths]

    with _patch_find_files(docs):
        content, artifact = await _glob(
            runtime=_make_runtime(), offset=chat_settings.REPO_GLOB_MAX_RESULTS
        )

    assert "showing" in content
    assert f"of {len(paths)}" in content


async def test_glob_recursive_pattern() -> None:
    """Recursive pattern `**/*.py` matches files at any depth."""
    docs = [
        _mock_file_doc("src/main.py"),
        _mock_file_doc("src/utils/helper.py"),
        _mock_file_doc("tests/test_main.py"),
    ]

    with _patch_find_files(docs, sorted_query=True):
        content, artifact = await _glob(runtime=_make_runtime(), pattern="**/*.py")

    assert "src/main.py" in content
    assert "src/utils/helper.py" in content
    assert "tests/test_main.py" in content
    assert len(artifact) == 3
    artifact_paths = {ref["path"] for ref in artifact}
    assert artifact_paths == {
        "src/main.py",
        "src/utils/helper.py",
        "tests/test_main.py",
    }


# ---------------------------------------------------------------------------
# _truncate_content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "limit"),
    [
        ("hello", 100),
        ("a" * 200, 100),
    ],
    ids=["within_limit", "over_limit"],
)
def test_truncate_content(content: str, limit: int) -> None:
    """Content is returned unchanged when within limit, truncated with marker when over."""
    result = _truncate_content(content, limit)
    if len(content) <= limit:
        assert result == content
    else:
        assert result.startswith(content[:limit])
        assert result.endswith(f"[...truncated, {len(content)} chars total]")


# ---------------------------------------------------------------------------
# Tool coroutine aliases
# ---------------------------------------------------------------------------

_read_file = repo_read_file.coroutine  # type: ignore[attr-defined]
_read_file_doc = repo_read_file_documentation.coroutine  # type: ignore[attr-defined]
_read_graph = repo_read_file_graph.coroutine  # type: ignore[attr-defined]
_read_meta = repo_read_metadata.coroutine  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Cross-tool: file not found
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("coro", "kwargs"),
    [
        (_read_file, {"path": "missing.py"}),
        (_read_file_doc, {"path": "missing.py"}),
        (_read_graph, {"path": "missing.py", "graph_type": "ast"}),
    ],
    ids=["read_file", "read_file_documentation", "read_file_graph"],
)
async def test_file_not_found_returns_error(coro: Any, kwargs: dict[str, Any]) -> None:
    """All file-reading tools return 'File not found' when FileDocument is missing."""
    with _patch_find_one("app.chat.tools.FileDocument.find_one", None):
        content, artifact = await coro(runtime=_make_runtime(), **kwargs)

    assert "File not found: missing.py" in content
    assert artifact == []


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


async def test_read_file_repo_not_found() -> None:
    """Returns 'Repository not found' when RepoDocument does not exist."""
    file_doc = _mock_file_doc("src/main.py")

    with (
        _patch_find_one("app.chat.tools.FileDocument.find_one", file_doc),
        _patch_find_one("app.chat.tools.RepoDocument.find_one", None),
    ):
        content, artifact = await _read_file(
            runtime=_make_runtime(), path="src/main.py"
        )

    assert content == "Repository not found."
    assert artifact == []


async def test_read_file_returns_content() -> None:
    """Returns formatted source code with file ref artifact."""
    file_doc = _mock_file_doc("src/main.py", file_id="bbbbbbbbbbbbbbbbbbbbbbbb")
    repo_doc = _mock_repo_doc("repos/abc123/code")

    with (
        _patch_find_one("app.chat.tools.FileDocument.find_one", file_doc),
        _patch_find_one("app.chat.tools.RepoDocument.find_one", repo_doc),
        _mock_blob_download("print('hello')"),
    ):
        content, artifact = await _read_file(
            runtime=_make_runtime(), path="src/main.py"
        )

    assert "### src/main.py" in content
    assert "print('hello')" in content
    assert len(artifact) == 1
    assert artifact[0]["path"] == "src/main.py"
    assert artifact[0]["file_id"] == "bbbbbbbbbbbbbbbbbbbbbbbb"


async def test_read_file_blob_error() -> None:
    """Raises RuntimeError when blob download fails."""
    file_doc = _mock_file_doc("src/main.py")
    repo_doc = _mock_repo_doc()

    mock_container = MagicMock()
    mock_container.download_blob = AsyncMock(side_effect=RuntimeError("blob down"))

    with (
        _patch_find_one("app.chat.tools.FileDocument.find_one", file_doc),
        _patch_find_one("app.chat.tools.RepoDocument.find_one", repo_doc),
        patch("app.chat.tools.get_container_client", return_value=mock_container),
        pytest.raises(RuntimeError, match="blob down"),
    ):
        await _read_file(runtime=_make_runtime(), path="src/main.py")


async def test_read_file_truncates_long_content() -> None:
    """Long file content is truncated with a marker."""
    max_len = chat_settings.REPO_READ_FILE_MAX_CONTENT_LENGTH
    long_source = "x" * (max_len + 500)

    file_doc = _mock_file_doc("big.py")
    repo_doc = _mock_repo_doc()

    with (
        _patch_find_one("app.chat.tools.FileDocument.find_one", file_doc),
        _patch_find_one("app.chat.tools.RepoDocument.find_one", repo_doc),
        _mock_blob_download(long_source),
    ):
        content, artifact = await _read_file(runtime=_make_runtime(), path="big.py")

    assert "truncated" in content
    assert f"{len(long_source)} chars total" in content
    assert len(artifact) == 1


# ---------------------------------------------------------------------------
# read_file_documentation
# ---------------------------------------------------------------------------


async def test_read_file_documentation_not_found() -> None:
    """Returns 'No documentation found' when documentation is missing."""
    file_doc = _mock_file_doc("src/main.py")

    with (
        _patch_find_one("app.chat.tools.FileDocument.find_one", file_doc),
        _patch_find_one("app.chat.tools.FileDocumentationDocument.find_one", None),
    ):
        content, artifact = await _read_file_doc(
            runtime=_make_runtime(), path="src/main.py"
        )

    assert "No documentation found for src/main.py" in content
    assert artifact == []


async def test_read_file_documentation_returns_content() -> None:
    """Returns documentation content with file ref artifact."""
    file_doc = _mock_file_doc("src/main.py", file_id="bbbbbbbbbbbbbbbbbbbbbbbb")
    doc = _mock_doc_content("This module is the main entry point.")

    with (
        _patch_find_one("app.chat.tools.FileDocument.find_one", file_doc),
        _patch_find_one("app.chat.tools.FileDocumentationDocument.find_one", doc),
    ):
        content, artifact = await _read_file_doc(
            runtime=_make_runtime(), path="src/main.py"
        )

    assert "### src/main.py" in content
    assert "This module is the main entry point." in content
    assert len(artifact) == 1
    assert artifact[0]["path"] == "src/main.py"
    assert artifact[0]["file_id"] == "bbbbbbbbbbbbbbbbbbbbbbbb"


# ---------------------------------------------------------------------------
# read_file_graph
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("graph_type", "make_patch", "expected_msg"),
    [
        (
            "ast",
            lambda: _patch_find_one("app.chat.tools.ASTDocument.find_one", None),
            "No AST graph found for src/main.py",
        ),
        (
            "dfg",
            lambda: _patch_find_graph_docs("app.chat.tools.DFGDocument.find", []),
            "No DFG graphs found for src/main.py",
        ),
    ],
    ids=["ast", "dfg"],
)
async def test_read_file_graph_not_found(
    graph_type: str,
    make_patch: Any,
    expected_msg: str,
) -> None:
    """Returns 'No <type> graph(s) found' when graph data is missing."""
    file_doc = _mock_file_doc("src/main.py")

    with (
        _patch_find_one("app.chat.tools.FileDocument.find_one", file_doc),
        make_patch(),
    ):
        content, artifact = await _read_graph(
            runtime=_make_runtime(), path="src/main.py", graph_type=graph_type
        )

    assert expected_msg in content
    assert artifact == []


async def test_read_file_graph_ast_returns_content() -> None:
    """Returns formatted AST JSON with file ref artifact."""
    file_doc = _mock_file_doc("src/main.py", file_id="bbbbbbbbbbbbbbbbbbbbbbbb")
    ast_doc = _mock_graph_doc({"type": "Program", "children": []})

    with (
        _patch_find_one("app.chat.tools.FileDocument.find_one", file_doc),
        _patch_find_one("app.chat.tools.ASTDocument.find_one", ast_doc),
    ):
        content, artifact = await _read_graph(
            runtime=_make_runtime(), path="src/main.py", graph_type="ast"
        )

    assert "### src/main.py (AST)" in content
    assert '"type": "Program"' in content
    assert len(artifact) == 1
    assert artifact[0]["file_id"] == "bbbbbbbbbbbbbbbbbbbbbbbb"


async def test_read_file_graph_cfg_returns_scoped() -> None:
    """Returns CFG graphs grouped by scope."""
    file_doc = _mock_file_doc("src/main.py")
    cfg_docs = [
        _mock_graph_doc({"nodes": [1, 2]}, scope="main"),
        _mock_graph_doc({"nodes": [3]}, scope="helper"),
    ]

    with (
        _patch_find_one("app.chat.tools.FileDocument.find_one", file_doc),
        _patch_find_graph_docs("app.chat.tools.CFGDocument.find", cfg_docs),
    ):
        content, artifact = await _read_graph(
            runtime=_make_runtime(), path="src/main.py", graph_type="cfg"
        )

    assert "### src/main.py (CFG)" in content
    assert '"scope": "main"' in content
    assert '"scope": "helper"' in content
    assert len(artifact) == 1


async def test_read_file_graph_truncates_large_graph() -> None:
    """Large graph data is truncated with a marker."""
    max_len = chat_settings.REPO_READ_FILE_GRAPH_MAX_CONTENT_LENGTH
    large_content = {"data": "x" * (max_len + 500)}

    file_doc = _mock_file_doc("big.py")
    ast_doc = _mock_graph_doc(large_content)

    with (
        _patch_find_one("app.chat.tools.FileDocument.find_one", file_doc),
        _patch_find_one("app.chat.tools.ASTDocument.find_one", ast_doc),
    ):
        content, artifact = await _read_graph(
            runtime=_make_runtime(), path="big.py", graph_type="ast"
        )

    assert "truncated" in content
    assert len(artifact) == 1


# ---------------------------------------------------------------------------
# read_repo_metadata
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("meta_doc", "expected"),
    [
        (None, "No metadata found for this repository."),
        (
            _mock_doc_content("A Java Spring Boot application for e-commerce."),
            "A Java Spring Boot application for e-commerce.",
        ),
    ],
    ids=["not_found", "returns_content"],
)
async def test_read_repo_metadata(meta_doc: Any, expected: str) -> None:
    """Returns metadata content or 'No metadata found' message."""
    with _patch_find_one("app.chat.tools.MetaRepoDocument.find_one", meta_doc):
        result = await _read_meta(runtime=_make_runtime())

    assert result == expected
