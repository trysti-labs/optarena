"""
optarena/drivers/autogen_sdk.py
------------------------------------
Optional driver: a minimal AutoGen/AG2 AssistantAgent (single agent, one run
per prompt) pointed at the backend's OpenAI-compatible endpoint. Requires
`pip install autogen-agentchat autogen-ext[openai]`.

Same shape as crewai_sdk.py: the agent has no tools, so it is asked to
output the complete file in one code block; the driver writes it to the
expected path - this measures the SDK's own orchestration/prompting stack on
top of the backend, not a tool-using agent. AutoGen's chat client is async
throughout, so each prompt is run via `asyncio.run`.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from ..cases import changed_files, evaluate_case, prepare_workspace, snapshot
from ..scenario import Scenario
from .base import CaseResult, Driver
from .openai_chat import concrete_target

_CODE_BLOCK = re.compile(r"```(?:\w+[^\n]*)?\n(.*?)```", re.DOTALL)

# AutoGen validates the target model against known OpenAI capability
# profiles unless told otherwise - a local/proxied model (Ollama, a router)
# isn't in that table, so model_info must be supplied explicitly. No tools
# are given to the agent, so function_calling/vision/json_output are all
# irrelevant to what this driver actually asks it to do.
_LOCAL_MODEL_INFO = {
    "vision": False, "function_calling": False,
    "json_output": False, "family": "unknown", "structured_output": False,
}


class AutoGenDriver(Driver):
    name = "autogen"

    def prepare(self, scenario: Scenario, workspace: Path) -> None:
        try:
            import autogen_agentchat  # noqa: F401
            import autogen_ext.models.openai  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "autogen not installed - pip install autogen-agentchat autogen-ext[openai]"
            ) from exc

    def run_case(self, case: dict, scenario: Scenario, workspace: Path) -> CaseResult:
        from autogen_agentchat.agents import AssistantAgent
        from autogen_ext.models.openai import OpenAIChatCompletionClient

        result = CaseResult(name=case["name"])
        backend = scenario.backend
        prepare_workspace(workspace, case)
        before = snapshot(workspace)

        expected = case.get("expected_files", [])
        target = concrete_target(expected[0]["path_pattern"] if expected else None)

        async def _run_prompts() -> str | None:
            client = OpenAIChatCompletionClient(
                model=backend.model,
                base_url=backend.openai_base,
                api_key=backend.api_key or "optarena",
                model_info=_LOCAL_MODEL_INFO,
            )
            try:
                agent = AssistantAgent(
                    name="software_engineer",
                    model_client=client,
                    system_message="You are a precise engineer who answers with one fenced "
                                    "code block containing the complete file content, and "
                                    "nothing else.",
                )
                for prompt in case.get("prompts", []):
                    context = ""
                    if (workspace / target).exists():
                        context = (
                            f"\nCurrent content of {target.name}:\n```\n"
                            f"{(workspace / target).read_text(encoding='utf-8', errors='replace')}\n```"
                        )
                    task_result = await agent.run(
                        task=prompt + context +
                        "\nReply with exactly one fenced code block containing the full file."
                    )
                    out = str(task_result.messages[-1].to_text())
                    blocks = _CODE_BLOCK.findall(out)
                    content = blocks[0].strip() + "\n" if blocks else out.strip() + "\n"
                    dest = workspace / target
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(content, encoding="utf-8")
                return None
            finally:
                await client.close()

        t0 = time.monotonic()
        try:
            asyncio.run(_run_prompts())
        except Exception as exc:  # noqa: BLE001
            result.error = f"{type(exc).__name__}: {exc}"
        result.duration_s = time.monotonic() - t0

        result.files = changed_files(before, workspace)
        result.failures, result.extra["oracle"] = evaluate_case(case, result.files, workspace)
        result.passed = result.error is None and not result.failures
        return result
