"""Deep analysis LLM.

This module manages the deep analysis agent.
"""

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware.subagents import (
    DEFAULT_GENERAL_PURPOSE_DESCRIPTION,
)
from deepagents.middleware.summarization import (
    create_summarization_tool_middleware,
)
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model
from langgraph.graph.state import CompiledStateGraph
from loguru import logger

from app.chat.config import chat_settings
from app.chat.llm import summarization_model
from app.chat.schemas import ChatContext
from app.chat.tools import (
    repo_glob,
    repo_read_file,
    repo_read_file_documentation,
    repo_read_file_graph,
    repo_read_metadata,
    repo_search_docs,
)
from app.deep_analysis.config import deep_analysis_settings
from app.deep_analysis.prompts import (
    DEEP_ANALYSIS_SUBAGENT_PROMPT,
    deep_analysis_system_prompt,
)
from app.deep_analysis.tools import DeepAnalysisReport

_deep_agent: CompiledStateGraph | None = None  # type: ignore[type-arg]


deep_analysis_model = (
    init_chat_model(
        model=deep_analysis_settings.DEEP_ANALYSIS_MODEL_NAME,
        model_provider=deep_analysis_settings.DEEP_ANALYSIS_MODEL_PROVIDER,
        base_url=deep_analysis_settings.DEEP_ANALYSIS_MODEL_BASE_URL,
        api_key=deep_analysis_settings.DEEP_ANALYSIS_MODEL_API_KEY.get_secret_value(),
        temperature=deep_analysis_settings.DEEP_ANALYSIS_MODEL_TEMPERATURE,
    )
    if deep_analysis_settings.DEEP_ANALYSIS_MODEL_BASE_URL
    and deep_analysis_settings.DEEP_ANALYSIS_MODEL_API_KEY
    else init_chat_model(
        model=chat_settings.CHAT_MODEL_NAME,
        model_provider=chat_settings.CHAT_MODEL_PROVIDER,
        base_url=chat_settings.CHAT_MODEL_BASE_URL,
        api_key=chat_settings.CHAT_MODEL_API_KEY.get_secret_value(),
        temperature=deep_analysis_settings.DEEP_ANALYSIS_MODEL_TEMPERATURE,
    )
)


def init_deep_agent_resources() -> None:
    """Initialize the LangGraph agent.

    Creates a LangGraph deep agent specialized for deep analysis tasks.
    """
    global _deep_agent

    logger.info("Deep analysis: Initializing deep agent resources...")

    middleware = [
        create_summarization_tool_middleware(summarization_model, StateBackend),  # type: ignore[arg-type]
        deep_analysis_system_prompt,
        ModelRetryMiddleware(
            on_failure="error",
        ),
        ModelCallLimitMiddleware(
            run_limit=deep_analysis_settings.DEEP_AGENT_MODEL_CALL_LIMIT,
            exit_behavior="error",
        ),
        ToolCallLimitMiddleware(
            run_limit=deep_analysis_settings.DEEP_AGENT_TOOL_CALL_LIMIT,
        ),
        ToolCallLimitMiddleware(
            tool_name="task",
            run_limit=deep_analysis_settings.DEEP_AGENT_TASK_CALL_LIMIT,
        ),
        ToolRetryMiddleware(),
    ]

    fallback = None
    if (
        deep_analysis_settings.DEEP_ANALYSIS_FALLBACK_MODEL_BASE_URL
        and deep_analysis_settings.DEEP_ANALYSIS_FALLBACK_MODEL_API_KEY
    ):
        # Explicit deep agent fallback
        fallback = init_chat_model(
            model=deep_analysis_settings.DEEP_ANALYSIS_FALLBACK_MODEL_NAME,
            model_provider=deep_analysis_settings.DEEP_ANALYSIS_FALLBACK_MODEL_PROVIDER,
            base_url=deep_analysis_settings.DEEP_ANALYSIS_FALLBACK_MODEL_BASE_URL,
            api_key=deep_analysis_settings.DEEP_ANALYSIS_FALLBACK_MODEL_API_KEY.get_secret_value(),
            temperature=deep_analysis_settings.DEEP_ANALYSIS_FALLBACK_MODEL_TEMPERATURE,
        )
    elif (
        deep_analysis_settings.DEEP_ANALYSIS_MODEL_BASE_URL
        and deep_analysis_settings.DEEP_ANALYSIS_MODEL_API_KEY
    ):
        # Deep agent has its own model → chat_model is a different endpoint
        fallback = init_chat_model(  # type: ignore[assignment]
            model=chat_settings.CHAT_MODEL_NAME,
            model_provider=chat_settings.CHAT_MODEL_PROVIDER,
            base_url=chat_settings.CHAT_MODEL_BASE_URL,
            api_key=chat_settings.CHAT_MODEL_API_KEY.get_secret_value(),
            temperature=deep_analysis_settings.DEEP_ANALYSIS_MODEL_TEMPERATURE,
        )
    elif (
        chat_settings.CHAT_FALLBACK_MODEL_BASE_URL
        and chat_settings.CHAT_FALLBACK_MODEL_API_KEY
    ):
        # Deep agent reuses chat provider → use chat's fallback for resilience
        fallback = init_chat_model(
            model=chat_settings.CHAT_FALLBACK_MODEL_NAME,
            model_provider=chat_settings.CHAT_FALLBACK_MODEL_PROVIDER,
            base_url=chat_settings.CHAT_FALLBACK_MODEL_BASE_URL,
            api_key=chat_settings.CHAT_FALLBACK_MODEL_API_KEY.get_secret_value(),
            temperature=deep_analysis_settings.DEEP_ANALYSIS_MODEL_TEMPERATURE,
        )

    if fallback is not None:
        middleware.append(ModelFallbackMiddleware(fallback))  # type: ignore[arg-type]

    _deep_agent = create_deep_agent(
        deep_analysis_model,  # type: ignore[arg-type]
        tools=[
            repo_glob,
            repo_read_file,
            repo_read_file_documentation,
            repo_read_file_graph,
            repo_read_metadata,
            repo_search_docs,
        ],
        middleware=middleware,  # type: ignore[arg-type]
        context_schema=ChatContext,  # type: ignore[arg-type]
        response_format=ToolStrategy(DeepAnalysisReport),
        subagents=[
            {
                "name": "general-purpose",
                "description": DEFAULT_GENERAL_PURPOSE_DESCRIPTION,
                "system_prompt": DEEP_ANALYSIS_SUBAGENT_PROMPT,
                "middleware": [
                    ToolCallLimitMiddleware(  # type: ignore[list-item]
                        run_limit=deep_analysis_settings.DEEP_AGENT_SUBAGENT_TOOL_CALL_LIMIT,
                    ),
                    ModelCallLimitMiddleware(  # type: ignore[list-item]
                        run_limit=deep_analysis_settings.DEEP_AGENT_SUBAGENT_MODEL_CALL_LIMIT,
                        exit_behavior="end",
                    ),
                    ToolRetryMiddleware(max_retries=1),
                ],
            },
        ],
    )

    logger.info("Deep analysis: Deep agent resources initialized.")


def close_deep_agent_resources() -> None:
    """Reset the deep analysis agent."""
    global _deep_agent

    logger.info("Deep analysis: Closing deep agent resources...")

    _deep_agent = None

    logger.info("Deep analysis: Deep agent resources closed.")


def get_deep_agent() -> CompiledStateGraph:  # type: ignore[type-arg]
    """Return the initialized deep agent.

    Raises:
        RuntimeError: If `init_deep_agent_resources()` has not been called yet.
    """
    if _deep_agent is None:
        raise RuntimeError(
            "Deep agent not initialized. Call init_deep_agent_resources() first."
        )
    return _deep_agent
