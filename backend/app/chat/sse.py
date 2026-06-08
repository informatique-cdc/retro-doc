"""Chat SSE utilities.

This module provides utility functions for creating Server-Sent Events (SSE) objects.
Each helper emits a distinct event type (``token``, ``error``, ``done``) so the
frontend can dispatch on the event name rather than parsing payloads.
"""

from typing import Any, Literal

from fastapi.sse import ServerSentEvent


def sse_chat_id(chat_id: str) -> ServerSentEvent:
    """Format a new chat thread ID as an SSE event.

    Args:
        chat_id(str): The string representation of the new thread's ObjectId.

    Returns:
        ServerSentEvent: An SSE object with event type `chat_id`.
    """
    return ServerSentEvent(data={"chat_id": chat_id}, event="chat_id")


def sse_done() -> ServerSentEvent:
    """Format a termination signal as an SSE event.

    For compatibility with the OpenAI streaming format, the data field
    is set to the string `[DONE]`, but the frontend should rely on the
    event type rather than parsing the data.

    Returns:
        ServerSentEvent: An SSE object with event type `done`.
    """
    return ServerSentEvent(raw_data="[DONE]", event="done")


def sse_error(detail: str) -> ServerSentEvent:
    """Format an error message as an SSE event.

    For compatibility with the OpenAI streaming format, the data field
    is set to a JSON object with an "error" key, but the frontend should
    rely on the event type rather than parsing the data.

    Args:
        detail(str): A user-facing error description.

    Returns:
        ServerSentEvent: An SSE object with event type `error`.
    """
    return ServerSentEvent(data={"error": detail}, event="error")


def sse_title(title: str) -> ServerSentEvent:
    """Format a generated chat title as an SSE event.

    Args:
        title(str): The generated title for the chat thread.

    Returns:
        ServerSentEvent: An SSE object with event type `title`.
    """
    return ServerSentEvent(data={"title": title}, event="title")


def sse_token(token: str) -> ServerSentEvent:
    """Format a token as an SSE event.

    For compatibility with the OpenAI streaming format, the data field
    is set to a JSON object with a "token" key, but the frontend should
    rely on the event type rather than parsing the data.

    Args:
        token(str): A single token from the model stream.

    Returns:
        ServerSentEvent: An SSE object with the payload serialized
            as the data field.
    """
    return ServerSentEvent(data={"token": token})


def sse_tool_start(tool: str, id: str) -> ServerSentEvent:
    """Format a tool invocation start as an SSE event.

    Args:
        tool(str): The name of the tool being invoked.
        id(str): The unique execution ID for this tool call.

    Returns:
        ServerSentEvent: An SSE object with event type `tool_start`.
    """
    return ServerSentEvent(data={"tool": tool, "id": id}, event="tool_start")


def sse_tool_end(
    tool: str,
    id: str,
    status: Literal["success", "error"],
    sources: list[dict[str, str]] | None = None,
) -> ServerSentEvent:
    """Format a tool invocation end as an SSE event.

    When the tool references files (e.g. `glob`, `search_repo_docs`),
    `sources` carries structured `{path, file_id}` pairs so the
    frontend can make file mentions clickable.

    Args:
        tool(str): The name of the tool that finished.
        id(str): The unique execution ID for this tool call.
        status(Literal["success", "error"]): The execution status of the tool.
        sources(list[dict[str, str]] | None): Optional file references
            produced by the tool, each containing `path` and `file_id`.

    Returns:
        ServerSentEvent: An SSE object with event type `tool_end`.
    """
    data: dict[str, Any] = {"tool": tool, "id": id, "status": status}
    if sources:
        data["sources"] = sources
    return ServerSentEvent(data=data, event="tool_end")
