"""
optarena/drivers/openai_agents_sdk.py
--------------------------------------
Optional driver: a minimal OpenAI Agents SDK agent (single agent, one run
per prompt) pointed at the backend's OpenAI-compatible endpoint. Exercises
the SDK-agent path rather than a CLI tool. Requires `pip install openai-agents`.

Same shape as crewai_sdk.py: the agent has no file tools, so it is asked to
output the complete file in one code block; the driver writes it to the
expected path - this measures the SDK's own orchestration/prompting stack on
top of the backend, not a tool-using agent.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from ..cases import changed_files, evaluate_case, prepare_workspace, snapshot
from ..scenario import Scenario
from .base import CaseResult, Driver
from .openai_chat import concrete_target

_CODE_BLOCK = re.compile(r"```(?:\w+[^\n]*)?\n(.*?)```", re.DOTALL)


class OpenAIAgentsDriver(Driver):
    name = "openai-agents"

    def prepare(self, scenario: Scenario, workspace: Path) -> None:
        try:
            import agents  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("openai-agents not installed - pip install openai-agents") from exc

    def run_case(self, case: dict, scenario: Scenario, workspace: Path) -> CaseResult:
        from agents import Agent, OpenAIChatCompletionsModel, RunConfig, Runner
        from openai import AsyncOpenAI

        result = CaseResult(name=case["name"])
        backend = scenario.backend
        prepare_workspace(workspace, case)
        before = snapshot(workspace)

        expected = case.get("expected_files", [])
        target = concrete_target(expected[0]["path_pattern"] if expected else None)

        # Explicit client, not env vars (OPENAI_API_KEY/OPENAI_BASE_URL) - a
        # real host key must never silently shadow (or be shadowed by) the
        # scenario's own backend, and nothing here should leak into the
        # process environment for later drivers in the same run.
        client = AsyncOpenAI(base_url=backend.openai_base, api_key=backend.api_key or "optarena")
        model = OpenAIChatCompletionsModel(model=backend.model, openai_client=client)
        agent = Agent(
            name="Software Engineer",
            instructions="You are a precise engineer who answers with one fenced code block "
                         "containing the complete file content, and nothing else.",
            model=model,
        )
        # Tracing defaults to ON and, independently of the explicit client
        # above, reads the host's ambient OPENAI_API_KEY (not backend.api_key)
        # to export every prompt/completion to https://api.openai.com - a
        # scenario aimed at a local Ollama backend would otherwise silently
        # ship its content to OpenAI whenever that env var happens to be set.
        run_config = RunConfig(tracing_disabled=True)

        t0 = time.monotonic()
        try:
            for prompt in case.get("prompts", []):
                context = ""
                if (workspace / target).exists():
                    context = (
                        f"\nCurrent content of {target.name}:\n```\n"
                        f"{(workspace / target).read_text(encoding='utf-8', errors='replace')}\n```"
                    )
                run_result = Runner.run_sync(
                    agent,
                    prompt + context +
                    "\nReply with exactly one fenced code block containing the full file.",
                    run_config=run_config,
                )
                out = str(run_result.final_output)
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
