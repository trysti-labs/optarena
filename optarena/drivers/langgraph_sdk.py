"""
optarena/drivers/langgraph_sdk.py
------------------------------------
Optional driver: LangGraph's prebuilt ReAct agent (single agent, one run per
prompt) pointed at the backend's OpenAI-compatible endpoint. Requires
`pip install langgraph langchain-openai`.

Same shape as crewai_sdk.py: the agent has no tools, so it is asked to
output the complete file in one code block; the driver writes it to the
expected path - this measures the SDK's own orchestration/prompting stack on
top of the backend, not a tool-using agent.
"""

from __future__ import annotations

import os
import re
import time
import warnings
from pathlib import Path

from ..cases import changed_files, evaluate_case, prepare_workspace, snapshot
from ..scenario import Scenario
from .base import CaseResult, Driver
from .openai_chat import concrete_target

_CODE_BLOCK = re.compile(r"```(?:\w+[^\n]*)?\n(.*?)```", re.DOTALL)


class LangGraphDriver(Driver):
    name = "langgraph"

    def prepare(self, scenario: Scenario, workspace: Path) -> None:
        # LangSmith tracing is off by default, but a developer who already
        # has it enabled globally for other LangChain work would otherwise
        # have every OptArena prompt/response traced there too - override
        # (not just default) all four var names langsmith checks, and do it
        # before any langchain/langgraph import: langsmith.utils.get_env_var
        # is lru_cache'd, so a later override wouldn't take effect once
        # something has already read it.
        for _var in ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING_V2",
                     "LANGCHAIN_TRACING", "LANGSMITH_TRACING"):
            os.environ[_var] = "false"
        try:
            import langgraph  # noqa: F401
            import langchain_openai  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "langgraph not installed - pip install langgraph langchain-openai"
            ) from exc

    def run_case(self, case: dict, scenario: Scenario, workspace: Path) -> CaseResult:
        # create_react_agent moved to langchain.agents in newer langchain
        # versions; the plain `langchain` package (not just langchain-core)
        # isn't a project dependency, so stay on the prebuilt entry point and
        # just silence its deprecation warning rather than add that dep.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore")
            from langgraph.prebuilt import create_react_agent
        from langchain_openai import ChatOpenAI

        result = CaseResult(name=case["name"])
        backend = scenario.backend
        prepare_workspace(workspace, case)
        before = snapshot(workspace)

        expected = case.get("expected_files", [])
        target = concrete_target(expected[0]["path_pattern"] if expected else None)

        llm = ChatOpenAI(
            model_name=backend.model,
            openai_api_base=backend.openai_base,
            openai_api_key=backend.api_key or "optarena",
        )
        agent = create_react_agent(model=llm, tools=[])

        t0 = time.monotonic()
        try:
            for prompt in case.get("prompts", []):
                context = ""
                if (workspace / target).exists():
                    context = (
                        f"\nCurrent content of {target.name}:\n```\n"
                        f"{(workspace / target).read_text(encoding='utf-8', errors='replace')}\n```"
                    )
                full_prompt = (
                    prompt + context +
                    "\nReply with exactly one fenced code block containing the full file. "
                    "Answer directly - do not call any tools."
                )
                run_result = agent.invoke({"messages": [{"role": "user", "content": full_prompt}]})
                out = str(run_result["messages"][-1].content)
                blocks = _CODE_BLOCK.findall(out)
                content = blocks[0].strip() + "\n" if blocks else out.strip() + "\n"
                dest = workspace / target
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            result.error = f"{type(exc).__name__}: {exc}"
        result.duration_s = time.monotonic() - t0

        result.files = changed_files(before, workspace)
        result.failures, result.extra["oracle"] = evaluate_case(case, result.files, workspace)
        result.passed = result.error is None and not result.failures
        return result
