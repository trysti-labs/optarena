"""
optarena/drivers/langgraph_sdk.py
------------------------------------
Optional driver: LangGraph's prebuilt ReAct agent (single agent, one run per
prompt) pointed at the backend's OpenAI-compatible endpoint. Requires
`pip install langgraph langchain-openai`.

The agent has no tools, so it is asked to output the complete file in one code
block; the driver writes it to the expected path - this measures the SDK's own
orchestration/prompting stack on top of the backend, not a tool-using agent.

A-09: the case loop lives in `sdk_base.SingleFileSDKDriver`.
"""

from __future__ import annotations

import os
import warnings

from ..scenario import Scenario
from .sdk_base import SingleFileSDKDriver


class LangGraphDriver(SingleFileSDKDriver):
    name = "langgraph"
    import_names = ("langgraph", "langchain_openai")
    install_hint = "pip install langgraph langchain-openai"

    def configure_environment(self) -> None:
        # LangSmith tracing is off by default, but a developer who already
        # has it enabled globally for other LangChain work would otherwise
        # have every OptArena prompt/response traced there too - override
        # (not just default) all four var names langsmith checks, and do it
        # before any langchain/langgraph import: langsmith.utils.get_env_var
        # is lru_cache'd, so a later override wouldn't take effect once
        # something has already read it. prepare() calls this before the
        # import check for exactly that reason.
        for var in ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING_V2",
                    "LANGCHAIN_TRACING", "LANGSMITH_TRACING"):
            os.environ[var] = "false"

    def open_session(self, scenario: Scenario):
        # create_react_agent moved to langchain.agents in newer langchain
        # versions; the plain `langchain` package (not just langchain-core)
        # isn't a project dependency, so stay on the prebuilt entry point and
        # just silence its deprecation warning rather than add that dep.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore")
            from langgraph.prebuilt import create_react_agent
        from langchain_openai import ChatOpenAI

        backend = scenario.backend
        # P2-02: generation parameters - confirmed live that ChatOpenAI
        # declares temperature/top_p/seed as direct model fields (not just
        # passthrough model_kwargs), so these reach the actual request.
        kwargs = {}
        if backend.temperature is not None:
            kwargs["temperature"] = backend.temperature
        if backend.top_p is not None:
            kwargs["top_p"] = backend.top_p
        if backend.seed is not None:
            kwargs["seed"] = backend.seed
        llm = ChatOpenAI(
            model_name=backend.model,
            openai_api_base=backend.openai_base,
            openai_api_key=backend.api_key or "optarena",
            **kwargs,
        )
        return create_react_agent(model=llm, tools=[])

    def complete(self, session, prompt: str, scenario: Scenario,
                 timeout: float) -> "tuple[str, dict]":
        run_result = session.invoke({"messages": [
            {"role": "user",
             "content": prompt + " Answer directly - do not call any tools."},
        ]})
        messages = run_result["messages"]
        return str(messages[-1].content), _usage_from(messages)


def _usage_from(messages) -> dict:   # noqa: ANN001 - langchain-specific objects
    """Sum `usage_metadata` across the run's AI messages, when present.
    Empty dict when the backend reported none."""
    prompt_tokens = completion_tokens = 0
    seen = False
    for message in messages:
        usage = getattr(message, "usage_metadata", None) or {}
        if usage:
            seen = True
            prompt_tokens += usage.get("input_tokens", 0) or 0
            completion_tokens += usage.get("output_tokens", 0) or 0
    if not seen:
        return {}
    return {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
