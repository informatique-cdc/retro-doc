"""Unit tests for chat tools.

This module tests the tools exposed to the chat agent: the glob/regex and
truncation helpers, and the repository and conversation lookup tools.
"""

import re
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from beanie import Document, PydanticObjectId

from app.chat import tools
from app.chat.config import chat_settings
from app.chat.models import ChatMessageDocument
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
from app.docs.models import FileDocumentationDocument, RepoMetaDocument
from app.graphs.models import ASTDocument, CFGDocument, DFGDocument
from app.repos.models import FileDocument, RepoDocument


def _make_runtime(
    thread_id: str = "000000000000000000000010",
    repo_id: str = "000000000000000000000020",
) -> MagicMock:
    """Build a mock `ToolRuntime` with configurable thread/repo IDs."""
    runtime = MagicMock()
    runtime.config = {"configurable": {"thread_id": thread_id, "repo_id": repo_id}}
    return runtime


def _mock_file_doc(path: str, file_id: str = "aaaaaaaaaaaaaaaaaaaaaaaa") -> MagicMock:
    """Build a mock `FileDocument` with *path* and *id*."""
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


def _patch_find_files(
    monkeypatch: pytest.MonkeyPatch,
    docs: list[MagicMock],
    *,
    sorted_query: bool = False,
) -> MagicMock:
    """Stub `FileDocument.find(...).to_list()` or `.sort(...).to_list()`.

    When *sorted_query* is True, the chain includes `.sort(...)` (recursive mode).
    """
    cursor = MagicMock()
    if sorted_query:
        sorted_cursor = MagicMock()
        sorted_cursor.to_list = AsyncMock(return_value=docs)
        cursor.sort = MagicMock(return_value=sorted_cursor)
    else:
        cursor.to_list = AsyncMock(return_value=docs)

    find = MagicMock(return_value=cursor)
    monkeypatch.setattr(FileDocument, "find", find)
    return find


def _patch_find_messages(
    monkeypatch: pytest.MonkeyPatch, messages: list[MagicMock]
) -> MagicMock:
    """Stub `ChatMessageDocument.find(...).sort(...).to_list()` to give *messages*."""
    sorted_cursor = MagicMock()
    sorted_cursor.to_list = AsyncMock(return_value=messages)
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=sorted_cursor)

    find = MagicMock(return_value=cursor)
    monkeypatch.setattr(ChatMessageDocument, "find", find)
    return find


def _patch_find_one(
    monkeypatch: pytest.MonkeyPatch, model: type[Document], return_value: Any
) -> AsyncMock:
    """Stub `Model.find_one(...)` on *model* to resolve to *return_value*."""
    find_one = AsyncMock(return_value=return_value)
    monkeypatch.setattr(model, "find_one", find_one)
    return find_one


def _mock_repo_doc(blob_path: str = "repos/abc123/code") -> MagicMock:
    """Build a mock `RepoDocument` with *blob_path*."""
    doc = MagicMock()
    doc.blob_path = blob_path
    return doc


def _mock_blob_download(monkeypatch: pytest.MonkeyPatch, content: str) -> MagicMock:
    """Stub `get_container_client().download_blob()` to return *content*."""
    downloader = AsyncMock()
    downloader.readall = AsyncMock(return_value=content.encode())
    container = MagicMock()
    container.download_blob = AsyncMock(return_value=downloader)
    monkeypatch.setattr(
        tools, "get_container_client", MagicMock(return_value=container)
    )
    return container


def _mock_doc_content(content: str) -> MagicMock:
    """Build a mock document with a `content` attribute."""
    doc = MagicMock()
    doc.content = content
    return doc


def _mock_graph_doc(content: dict[str, Any], scope: str | None = None) -> MagicMock:
    """Build a mock graph document with `content` and optional `scope`."""
    doc = MagicMock()
    doc.content = content
    doc.scope = scope
    return doc


def _patch_find_graph_docs(
    monkeypatch: pytest.MonkeyPatch, model: type[Document], docs: list[MagicMock]
) -> MagicMock:
    """Stub `Model.find(...).to_list()` on *model* to return *docs*."""
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=docs)

    find = MagicMock(return_value=cursor)
    monkeypatch.setattr(model, "find", find)
    return find


_glob = repo_glob.coroutine  # type: ignore[attr-defined]
_read_file = repo_read_file.coroutine  # type: ignore[attr-defined]
_read_file_doc = repo_read_file_documentation.coroutine  # type: ignore[attr-defined]
_read_graph = repo_read_file_graph.coroutine  # type: ignore[attr-defined]
_read_meta = repo_read_metadata.coroutine  # type: ignore[attr-defined]
_retrieve = retrieve_messages.coroutine  # type: ignore[attr-defined]
_search = repo_search_docs.coroutine  # type: ignore[attr-defined]


class TestFileNotFoundAcrossTools:
    """Every file-reading tool reports a missing file the same way."""

    @pytest.mark.parametrize(
        ("coro", "kwargs"),
        [
            pytest.param(_read_file, {"path": "missing.py"}, id="read-file"),
            pytest.param(
                _read_file_doc, {"path": "missing.py"}, id="read-file-documentation"
            ),
            pytest.param(
                _read_graph,
                {"path": "missing.py", "graph_type": "ast"},
                id="read-file-graph",
            ),
        ],
    )
    async def test_file_not_found_returns_error(
        self, monkeypatch: pytest.MonkeyPatch, coro: Any, kwargs: dict[str, Any]
    ) -> None:
        """All file-reading tools return 'File not found' when `FileDocument` is missing."""
        _patch_find_one(monkeypatch, FileDocument, None)

        content, artifact = await coro(runtime=_make_runtime(), **kwargs)

        assert "File not found: missing.py" in content
        assert artifact == []


class TestGlobToRegex:
    """Translate a glob pattern into a MongoDB-compatible regex."""

    @pytest.mark.parametrize(
        ("pattern", "should_match", "should_not_match"),
        [
            pytest.param(
                "**/*.py",
                ["src/main.py", "a/b/c.py"],
                ["main.txt"],
                id="double-star-py",
            ),
            pytest.param(
                "**/test_*", ["test_foo", "a/b/test_bar"], [], id="double-star-prefix"
            ),
            pytest.param("?.py", ["a.py"], ["ab.py", "/.py"], id="question-mark"),
            pytest.param(
                "file.name+v2", ["file.name+v2"], ["fileXnameXv2"], id="special-chars"
            ),
            pytest.param(
                "src/*.ts", ["src/app.ts"], ["src/sub/app.ts"], id="single-star"
            ),
        ],
    )
    def test_glob_to_regex_matching(
        self,
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


class TestRepoGlob:
    """List or filter a repository's files by directory and glob pattern."""

    async def test_glob_directory_listing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lists immediate children (dirs + files) when no pattern is given."""
        docs = [
            _mock_file_doc("src/main.py"),
            _mock_file_doc("src/utils/helper.py"),
            _mock_file_doc("README.md"),
        ]
        _patch_find_files(monkeypatch, docs)

        content, artifact = await _glob(runtime=_make_runtime())

        assert "Contents of /" in content
        assert "src/" in content
        assert "README.md" in content
        # Only files (not directories) appear in the artifact
        artifact_paths = {ref["path"] for ref in artifact}
        assert "README.md" in artifact_paths

    async def test_glob_dirs_only_pattern(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pattern ending with `/` returns only directories."""
        docs = [
            _mock_file_doc("src/utils/helper.py"),
            _mock_file_doc("src/models/user.py"),
        ]
        _patch_find_files(monkeypatch, docs, sorted_query=True)

        content, artifact = await _glob(runtime=_make_runtime(), pattern="**/")

        assert "src/" in content
        assert "helper.py" not in content
        assert artifact == []

    async def test_glob_empty_results_no_pattern(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns 'No files found' when directory is empty."""
        _patch_find_files(monkeypatch, [])

        content, artifact = await _glob(runtime=_make_runtime())

        assert "No files found in repository root." in content
        assert artifact == []

    async def test_glob_empty_results_with_pattern(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns 'No entries matching' when pattern matches nothing."""
        _patch_find_files(monkeypatch, [], sorted_query=True)

        content, artifact = await _glob(runtime=_make_runtime(), pattern="**/*.rs")

        assert "No entries matching" in content
        assert artifact == []

    async def test_glob_non_recursive_pattern(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Filters immediate children with a non-recursive pattern."""
        docs = [
            _mock_file_doc("src/app.py"),
            _mock_file_doc("src/app.ts"),
            _mock_file_doc("src/utils/helper.py"),
        ]
        _patch_find_files(monkeypatch, docs)

        content, artifact = await _glob(
            runtime=_make_runtime(), directory="src", pattern="*.py"
        )

        assert "app.py" in content
        assert "app.ts" not in content
        artifact_paths = {ref["path"] for ref in artifact}
        assert "src/app.py" in artifact_paths

    async def test_glob_offset_out_of_range(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns message when offset is beyond total entries."""
        _patch_find_files(monkeypatch, [_mock_file_doc("file.py")])

        content, artifact = await _glob(runtime=_make_runtime(), offset=100)

        assert "No entries at offset 100" in content
        assert artifact == []

    async def test_glob_pagination_offset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Offset paginates through results."""
        paths = [f"file{i}.py" for i in range(chat_settings.REPO_GLOB_MAX_RESULTS + 5)]
        _patch_find_files(monkeypatch, [_mock_file_doc(p) for p in paths])

        content, _artifact = await _glob(
            runtime=_make_runtime(), offset=chat_settings.REPO_GLOB_MAX_RESULTS
        )

        assert "showing" in content
        assert f"of {len(paths)}" in content

    async def test_glob_recursive_pattern(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Recursive pattern `**/*.py` matches files at any depth."""
        docs = [
            _mock_file_doc("src/main.py"),
            _mock_file_doc("src/utils/helper.py"),
            _mock_file_doc("tests/test_main.py"),
        ]
        _patch_find_files(monkeypatch, docs, sorted_query=True)

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


class TestRepoReadFile:
    """Read a repository file's source out of blob storage."""

    async def test_read_file_repo_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns 'Repository not found' when RepoDocument does not exist."""
        _patch_find_one(monkeypatch, FileDocument, _mock_file_doc("src/main.py"))
        _patch_find_one(monkeypatch, RepoDocument, None)

        content, artifact = await _read_file(
            runtime=_make_runtime(), path="src/main.py"
        )

        assert content == "Repository not found."
        assert artifact == []

    async def test_read_file_returns_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns formatted source code with file ref artifact."""
        file_doc = _mock_file_doc("src/main.py", file_id="bbbbbbbbbbbbbbbbbbbbbbbb")
        _patch_find_one(monkeypatch, FileDocument, file_doc)
        _patch_find_one(monkeypatch, RepoDocument, _mock_repo_doc())
        _mock_blob_download(monkeypatch, "print('hello')")

        content, artifact = await _read_file(
            runtime=_make_runtime(), path="src/main.py"
        )

        assert "### src/main.py" in content
        assert "print('hello')" in content
        assert len(artifact) == 1
        assert artifact[0]["path"] == "src/main.py"
        assert artifact[0]["file_id"] == "bbbbbbbbbbbbbbbbbbbbbbbb"

    async def test_read_file_blob_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raises RuntimeError when blob download fails."""
        _patch_find_one(monkeypatch, FileDocument, _mock_file_doc("src/main.py"))
        _patch_find_one(monkeypatch, RepoDocument, _mock_repo_doc())

        container = MagicMock()
        container.download_blob = AsyncMock(side_effect=RuntimeError("blob down"))
        monkeypatch.setattr(
            tools, "get_container_client", MagicMock(return_value=container)
        )

        with pytest.raises(RuntimeError, match="blob down"):
            await _read_file(runtime=_make_runtime(), path="src/main.py")

    async def test_read_file_truncates_long_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Long file content is truncated with a marker."""
        max_len = chat_settings.REPO_READ_FILE_MAX_CONTENT_LENGTH
        long_source = "x" * (max_len + 500)
        _patch_find_one(monkeypatch, FileDocument, _mock_file_doc("big.py"))
        _patch_find_one(monkeypatch, RepoDocument, _mock_repo_doc())
        _mock_blob_download(monkeypatch, long_source)

        content, artifact = await _read_file(runtime=_make_runtime(), path="big.py")

        assert "truncated" in content
        assert f"{len(long_source)} chars total" in content
        assert len(artifact) == 1


class TestRepoReadFileDocumentation:
    """Read the generated documentation for a repository file."""

    async def test_read_file_documentation_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns 'No documentation found' when documentation is missing."""
        _patch_find_one(monkeypatch, FileDocument, _mock_file_doc("src/main.py"))
        _patch_find_one(monkeypatch, FileDocumentationDocument, None)

        content, artifact = await _read_file_doc(
            runtime=_make_runtime(), path="src/main.py"
        )

        assert "No documentation found for src/main.py" in content
        assert artifact == []

    async def test_read_file_documentation_returns_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns documentation content with file ref artifact."""
        file_doc = _mock_file_doc("src/main.py", file_id="bbbbbbbbbbbbbbbbbbbbbbbb")
        doc = _mock_doc_content("This module is the main entry point.")
        _patch_find_one(monkeypatch, FileDocument, file_doc)
        _patch_find_one(monkeypatch, FileDocumentationDocument, doc)

        content, artifact = await _read_file_doc(
            runtime=_make_runtime(), path="src/main.py"
        )

        assert "### src/main.py" in content
        assert "This module is the main entry point." in content
        assert len(artifact) == 1
        assert artifact[0]["path"] == "src/main.py"
        assert artifact[0]["file_id"] == "bbbbbbbbbbbbbbbbbbbbbbbb"


class TestRepoReadFileGraph:
    """Read a file's AST, CFG or DFG graph."""

    @pytest.mark.parametrize(
        ("graph_type", "apply_patch", "expected_msg"),
        [
            pytest.param(
                "ast",
                lambda mp: _patch_find_one(mp, ASTDocument, None),
                "No AST graph found for src/main.py",
                id="ast",
            ),
            pytest.param(
                "dfg",
                lambda mp: _patch_find_graph_docs(mp, DFGDocument, []),
                "No DFG graphs found for src/main.py",
                id="dfg",
            ),
        ],
    )
    async def test_read_file_graph_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        graph_type: str,
        apply_patch: Callable[[pytest.MonkeyPatch], Mock],
        expected_msg: str,
    ) -> None:
        """Returns 'No <type> graph(s) found' when graph data is missing."""
        _patch_find_one(monkeypatch, FileDocument, _mock_file_doc("src/main.py"))
        apply_patch(monkeypatch)

        content, artifact = await _read_graph(
            runtime=_make_runtime(), path="src/main.py", graph_type=graph_type
        )

        assert expected_msg in content
        assert artifact == []

    async def test_read_file_graph_ast_returns_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns formatted AST JSON with file ref artifact."""
        file_doc = _mock_file_doc("src/main.py", file_id="bbbbbbbbbbbbbbbbbbbbbbbb")
        ast_doc = _mock_graph_doc({"type": "Program", "children": []})
        _patch_find_one(monkeypatch, FileDocument, file_doc)
        _patch_find_one(monkeypatch, ASTDocument, ast_doc)

        content, artifact = await _read_graph(
            runtime=_make_runtime(), path="src/main.py", graph_type="ast"
        )

        assert "### src/main.py (AST)" in content
        assert '"type": "Program"' in content
        assert len(artifact) == 1
        assert artifact[0]["file_id"] == "bbbbbbbbbbbbbbbbbbbbbbbb"

    async def test_read_file_graph_cfg_returns_scoped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns CFG graphs grouped by scope."""
        cfg_docs = [
            _mock_graph_doc({"nodes": [1, 2]}, scope="main"),
            _mock_graph_doc({"nodes": [3]}, scope="helper"),
        ]
        _patch_find_one(monkeypatch, FileDocument, _mock_file_doc("src/main.py"))
        _patch_find_graph_docs(monkeypatch, CFGDocument, cfg_docs)

        content, artifact = await _read_graph(
            runtime=_make_runtime(), path="src/main.py", graph_type="cfg"
        )

        assert "### src/main.py (CFG)" in content
        assert '"scope": "main"' in content
        assert '"scope": "helper"' in content
        assert len(artifact) == 1

    async def test_read_file_graph_truncates_large_graph(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Large graph data is truncated with a marker."""
        max_len = chat_settings.REPO_READ_FILE_GRAPH_MAX_CONTENT_LENGTH
        ast_doc = _mock_graph_doc({"data": "x" * (max_len + 500)})
        _patch_find_one(monkeypatch, FileDocument, _mock_file_doc("big.py"))
        _patch_find_one(monkeypatch, ASTDocument, ast_doc)

        content, artifact = await _read_graph(
            runtime=_make_runtime(), path="big.py", graph_type="ast"
        )

        assert "truncated" in content
        assert len(artifact) == 1


class TestRepoReadMetadata:
    """Read the repository-level metadata document."""

    @pytest.mark.parametrize(
        ("meta_doc", "expected"),
        [
            pytest.param(
                None, "No metadata found for this repository.", id="not-found"
            ),
            pytest.param(
                _mock_doc_content("A Java Spring Boot application for e-commerce."),
                "A Java Spring Boot application for e-commerce.",
                id="returns-content",
            ),
        ],
    )
    async def test_read_repo_metadata(
        self, monkeypatch: pytest.MonkeyPatch, meta_doc: Any, expected: str
    ) -> None:
        """Returns metadata content or 'No metadata found' message."""
        _patch_find_one(monkeypatch, RepoMetaDocument, meta_doc)

        result = await _read_meta(runtime=_make_runtime())

        assert result == expected


class TestRepoSearchDocs:
    """Search the repository's documentation through the vectorstore."""

    async def test_search_repo_docs_empty_results(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns 'No documentation found' when VectorStore returns nothing."""
        vectorstore = AsyncMock()
        vectorstore.asimilarity_search = AsyncMock(return_value=[])
        monkeypatch.setattr(
            tools, "get_vectorstore", MagicMock(return_value=vectorstore)
        )

        content, artifact = await _search(runtime=_make_runtime(), query="nonexistent")

        assert content == "No documentation found for this repository."
        assert artifact == []

    async def test_search_repo_docs_returns_formatted_sections(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns formatted sections with file paths and content."""
        hit = MagicMock()
        hit.metadata = {"file_path": "src/main.py"}
        hit.page_content = "Main entry point."

        vectorstore = AsyncMock()
        vectorstore.asimilarity_search = AsyncMock(return_value=[hit])
        monkeypatch.setattr(
            tools, "get_vectorstore", MagicMock(return_value=vectorstore)
        )

        file_doc = _mock_file_doc("src/main.py", file_id="bbbbbbbbbbbbbbbbbbbbbbbb")
        _patch_find_files(monkeypatch, [file_doc])

        content, artifact = await _search(runtime=_make_runtime(), query="main entry")

        assert "### src/main.py" in content
        assert "Main entry point." in content
        assert len(artifact) == 1
        assert artifact[0]["path"] == "src/main.py"
        assert artifact[0]["file_id"] == "bbbbbbbbbbbbbbbbbbbbbbbb"


class TestRetrieveMessages:
    """Recall messages from the selected branch of the current conversation."""

    async def test_retrieve_messages_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns 'No messages found' when DB is empty."""
        _patch_find_messages(monkeypatch, [])

        result = await _retrieve(runtime=_make_runtime())

        assert result == "No messages found in this conversation."

    async def test_retrieve_messages_filters_by_query(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Passes regex query filter to the DB query."""
        find = _patch_find_messages(monkeypatch, [_mock_msg("human", "auth stuff")])

        await _retrieve(runtime=_make_runtime(), query="auth")

        call_args = find.call_args[0][0]
        assert "$regex" in call_args["content"]

    async def test_retrieve_messages_filters_by_role(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Passes role filter to the DB query."""
        find = _patch_find_messages(monkeypatch, [_mock_msg("human", "Hello")])

        result = await _retrieve(runtime=_make_runtime(), role="human")

        call_args = find.call_args[0][0]
        assert call_args["role"] == "human"
        assert "(filtered by role: human)" in result

    async def test_retrieve_messages_full_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """full_content=True disables truncation."""
        max_len = chat_settings.RETRIEVE_MESSAGES_MAX_CONTENT_LENGTH
        long_content = "a" * (max_len + 100)
        _patch_find_messages(monkeypatch, [_mock_msg("human", long_content)])

        result = await _retrieve(runtime=_make_runtime(), full_content=True)

        assert "truncated" not in result
        assert long_content in result

    async def test_retrieve_messages_negative_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative start counts from the end."""
        msgs = [_mock_msg("human", "First"), _mock_msg("ai", "Second")]
        _patch_find_messages(monkeypatch, msgs)

        result = await _retrieve(runtime=_make_runtime(), start=-1, limit=1)

        assert "#2 [ai]: Second" in result
        assert "Retrieved 1 of 2" in result

    async def test_retrieve_messages_out_of_range_position(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns position error when start is beyond available messages."""
        _patch_find_messages(monkeypatch, [_mock_msg("human", "Only one")])

        result = await _retrieve(runtime=_make_runtime(), start=5)

        assert "No messages at the requested position" in result

    async def test_retrieve_messages_returns_formatted_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns messages with positions and a header."""
        msgs = [_mock_msg("human", "Hello"), _mock_msg("ai", "Hi there!")]
        _patch_find_messages(monkeypatch, msgs)

        result = await _retrieve(runtime=_make_runtime())

        assert "Retrieved 2 of 2" in result
        assert "#1 [human]: Hello" in result
        assert "#2 [ai]: Hi there!" in result

    async def test_retrieve_messages_scoped_to_the_selected_branch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Answers the user regenerated away from must not be recalled."""
        find = _patch_find_messages(monkeypatch, [_mock_msg("human", "Hello")])

        await _retrieve(runtime=_make_runtime())

        call_args = find.call_args[0][0]
        assert call_args["active"] is True


class TestTruncateContent:
    """Cap a string at a maximum length, appending a truncation marker."""

    @pytest.mark.parametrize(
        ("content", "limit"),
        [
            pytest.param("hello", 100, id="within-limit"),
            pytest.param("a" * 200, 100, id="over-limit"),
        ],
    )
    def test_truncate_content(self, content: str, limit: int) -> None:
        """Content within the limit is unchanged; over it, a marker is appended."""
        result = _truncate_content(content, limit)
        if len(content) <= limit:
            assert result == content
        else:
            assert result.startswith(content[:limit])
            assert result.endswith(f"[...truncated, {len(content)} chars total]")
