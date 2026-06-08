"""Chat LLM.

This module manages the chat model, synchronous MongoDB client, LangGraph
checkpointer, and agent. The sync client is required because `MongoDBSaver`
from `langgraph-checkpoint-mongodb` only supports synchronous
`pymongo.MongoClient`. Under the hood, the agent's checkpointing operations
will be done asynchronously.
"""

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.graph.state import CompiledStateGraph
from loguru import logger
from pymongo import MongoClient

from app.chat.config import chat_settings
from app.chat.prompts import chat_system_prompt
from app.chat.schemas import ChatContext
from app.chat.tools import (
    repo_glob,
    repo_read_file,
    repo_read_file_documentation,
    repo_read_file_graph,
    repo_read_metadata,
    repo_search_docs,
    retrieve_messages,
)
from app.core.config import settings

_client: MongoClient | None = None  # type: ignore[type-arg]
_checkpointer: MongoDBSaver | None = None
_agent: CompiledStateGraph | None = None  # type: ignore[type-arg]

chat_model = init_chat_model(
    model=chat_settings.CHAT_MODEL_NAME,
    model_provider=chat_settings.CHAT_MODEL_PROVIDER,
    base_url=chat_settings.CHAT_MODEL_BASE_URL,
    api_key=chat_settings.CHAT_MODEL_API_KEY.get_secret_value(),
    temperature=chat_settings.CHAT_MODEL_TEMPERATURE,
)

summarization_model = (
    init_chat_model(
        model=chat_settings.SUMMARIZATION_MODEL_NAME,
        model_provider=chat_settings.SUMMARIZATION_MODEL_PROVIDER,
        base_url=chat_settings.SUMMARIZATION_MODEL_BASE_URL,
        api_key=chat_settings.SUMMARIZATION_MODEL_API_KEY.get_secret_value(),
        temperature=chat_settings.SUMMARIZATION_MODEL_TEMPERATURE,
    )
    if chat_settings.SUMMARIZATION_MODEL_BASE_URL
    and chat_settings.SUMMARIZATION_MODEL_API_KEY
    else chat_model
)

title_model = (
    init_chat_model(
        model=chat_settings.TITLE_MODEL_NAME,
        model_provider=chat_settings.TITLE_MODEL_PROVIDER,
        base_url=chat_settings.TITLE_MODEL_BASE_URL,
        api_key=chat_settings.TITLE_MODEL_API_KEY.get_secret_value(),
        temperature=chat_settings.TITLE_MODEL_TEMPERATURE,
    )
    if chat_settings.TITLE_MODEL_BASE_URL and chat_settings.TITLE_MODEL_API_KEY
    else chat_model
)


def init_agent_resources() -> None:
    """Initialize MongoDB client, checkpointer, and LangGraph agent.

    Creates a synchronous MongoClient (required by MongoDBSaver), sets up
    the MongoDBSaver checkpointer, and initializes the LangGraph agent
    with the chat model, dynamic system prompt, and checkpointer.
    """
    global _client, _checkpointer, _agent

    logger.info("Chat: Initializing agent resources...")

    _client = MongoClient(
        settings.MONGODB_CONNECTION_STR.get_secret_value(),
        socketTimeoutMS=chat_settings.MONGODB_SOCKET_TIMEOUT_MS,
        connectTimeoutMS=chat_settings.MONGODB_CONNECT_TIMEOUT_MS,
        serverSelectionTimeoutMS=chat_settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS,
        maxIdleTimeMS=chat_settings.MONGODB_MAX_IDLE_TIME_MS,
    )
    _checkpointer = MongoDBSaver(
        _client,
        db_name=settings.MONGODB_DB_NAME,
        checkpoint_collection_name="chat_checkpoints",
        writes_collection_name="chat_checkpoint_writes",
    )

    middleware = [
        chat_system_prompt,
        ModelRetryMiddleware(
            on_failure="error",
        ),
        SummarizationMiddleware(
            model=summarization_model,  # type: ignore[arg-type]
            trigger=chat_settings.AGENT_SUMMARIZATION_TRIGGER,  # type: ignore[arg-type]
            keep=chat_settings.AGENT_SUMMARIZATION_KEEP,  # type: ignore[arg-type]
        ),
        ModelCallLimitMiddleware(
            run_limit=chat_settings.AGENT_MODEL_CALL_LIMIT,
            exit_behavior="error",
        ),
        ToolCallLimitMiddleware(run_limit=chat_settings.AGENT_TOOL_CALL_LIMIT),
        ToolRetryMiddleware(),
    ]

    if (
        chat_settings.CHAT_FALLBACK_MODEL_BASE_URL
        and chat_settings.CHAT_FALLBACK_MODEL_API_KEY
    ):
        fallback_model = init_chat_model(
            model=chat_settings.CHAT_FALLBACK_MODEL_NAME,
            model_provider=chat_settings.CHAT_FALLBACK_MODEL_PROVIDER,
            base_url=chat_settings.CHAT_FALLBACK_MODEL_BASE_URL,
            api_key=chat_settings.CHAT_FALLBACK_MODEL_API_KEY.get_secret_value(),
            temperature=chat_settings.CHAT_FALLBACK_MODEL_TEMPERATURE,
        )
        middleware.append(ModelFallbackMiddleware(fallback_model))  # type: ignore[arg-type]

    _agent = create_agent(
        chat_model,
        tools=[
            repo_glob,
            repo_read_file,
            repo_read_file_documentation,
            repo_read_file_graph,
            repo_read_metadata,
            repo_search_docs,
            retrieve_messages,
        ],
        middleware=middleware,  # type: ignore[arg-type]
        context_schema=ChatContext,  # type: ignore[arg-type]
        checkpointer=_checkpointer,
    )

    logger.info("Chat: Agent resources initialized.")


def close_agent_resources() -> None:
    """Close the sync MongoDB client."""
    global _client, _checkpointer, _agent

    logger.info("Chat: Closing agent resources...")

    if _client is not None:
        _client.close()

    _client = None
    _checkpointer = None
    _agent = None

    logger.info("Chat: Agent resources closed.")


def get_agent() -> CompiledStateGraph:  # type: ignore[type-arg]
    """Return the initialized LangGraph agent.

    Raises:
        RuntimeError: If `init_agent_resources()` has not been called yet.
    """
    if _agent is None:
        raise RuntimeError(
            "Agent resources not initialized. Call init_agent_resources() first."
        )
    return _agent


def get_checkpointer() -> MongoDBSaver:
    """Return the initialized MongoDB checkpointer.

    Raises:
        RuntimeError: If `init_agent_resources()` has not been called yet.
    """
    if _checkpointer is None:
        raise RuntimeError(
            "Agent resources not initialized. Call init_agent_resources() first."
        )
    return _checkpointer
