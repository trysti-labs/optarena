"""
optarena/drivers/smolagents_sdk.py
------------------------------------
Optional driver: a minimal HuggingFace smolagents agent (single agent, one
run per prompt) pointed at the backend's OpenAI-compatible endpoint. Requires
`pip install smolagents`.

Uses `ToolCallingAgent` with no tools (not `CodeAgent`, which executes
model-written Python in this process - out of scope for a single-shot "write
one file" driver). Same shape as crewai_sdk.py: the agent is asked to output
the complete file in one code block; the driver writes it to the expected
path - this measures the SDK's own orchestration/prompting stack on top of
the backend, not a tool-using agent.
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


class SmolAgentsDriver(Driver):
    name = "smolagents"

    def prepare(self, scenario: Scenario, workspace: Path) -> None:
        try:
            import smolagents  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("smolagents not installed - pip install smolagents") from exc

    def run_case(self, case: dict, scenario: Scenario, workspace: Path) -> CaseResult:
        from smolagents import OpenAIServerModel, ToolCallingAgent

        result = CaseResult(name=case["name"])
        backend = scenario.backend
        prepare_workspace(workspace, case)
        before = snapshot(workspace)

        expected = case.get("expected_files", [])
        target = concrete_target(expected[0]["path_pattern"] if expected else None)

        model = OpenAIServerModel(
            model_id=backend.model,
            api_base=backend.openai_base,
            api_key=backend.api_key or "optarena",
        )

        t0 = time.monotonic()
        try:
            for prompt in case.get("prompts", []):
                context = ""
                if (workspace / target).exists():
                    context = (
                        f"\nCurrent content of {target.name}:\n```\n"
                        f"{(workspace / target).read_text(encoding='utf-8', errors='replace')}\n```"
                    )
                # Fresh agent per prompt (like the other SDK drivers) - no
                # cross-prompt tool-call state to carry, and reset=True on a
                # shared agent would drop the multi-prompt file context anyway.
                agent = ToolCallingAgent(tools=[], model=model, max_tool_threads=1)
                out = str(agent.run(
                    prompt + context +
                    "\nReply with exactly one fenced code block containing the full file.",
                    max_steps=4,
                ))
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
