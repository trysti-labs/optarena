"""
optarena/drivers/crewai_sdk.py
───────────────────────────
Optional driver: a minimal crewAI agent (single coder agent + task per prompt)
pointed at the backend's OpenAI-compatible endpoint. Exercises the SDK-agent
path rather than a UI or CLI. Requires `pip install crewai`.

The agent is asked to output the complete file in one code block; the driver
writes it to the expected path (same convention as the chat baseline) since a
bare crew has no file tools - this measures crewAI's orchestration + prompting
stack on top of the backend.
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


class CrewAIDriver(Driver):
    name = "crewai"

    def prepare(self, scenario: Scenario, workspace: Path) -> None:
        try:
            import crewai  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("crewai not installed - pip install crewai") from exc

    def run_case(self, case: dict, scenario: Scenario, workspace: Path) -> CaseResult:
        from crewai import Agent, Crew, Task, LLM

        result = CaseResult(name=case["name"])
        backend = scenario.backend
        prepare_workspace(workspace, case)
        before = snapshot(workspace)

        expected = case.get("expected_files", [])
        target = concrete_target(expected[0]["path_pattern"] if expected else None)

        # The key is passed explicitly to LLM() below - do NOT also write it
        # into os.environ: that leaked the scenario's key into this process's
        # environment for every later driver/subprocess of the run, and
        # conversely a pre-existing host OPENAI_API_KEY silently shadowed the
        # scenario's key for any crewAI internals reading the env.
        llm = LLM(
            model=f"openai/{backend.model}",
            base_url=backend.openai_base,
            api_key=backend.api_key,
        )
        coder = Agent(
            role="Software Engineer",
            goal="Produce complete, working source files exactly as requested.",
            backstory="A precise engineer who answers with one fenced code block.",
            llm=llm, verbose=False,
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
                task = Task(
                    description=prompt + context +
                    "\nReply with exactly one fenced code block containing the full file.",
                    expected_output="One fenced code block with the complete file content.",
                    agent=coder,
                )
                out = str(Crew(agents=[coder], tasks=[task], verbose=False).kickoff())
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
