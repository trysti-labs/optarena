"""
optarena/drivers/semantic_kernel_sdk.py
------------------------------------------
Optional driver: a minimal Semantic Kernel ChatCompletionAgent (single
agent, one run per prompt) pointed at the backend's OpenAI-compatible
endpoint. Requires `pip install semantic-kernel`.

Same shape as crewai_sdk.py: the agent has no plugins/tools, so it is asked
to output the complete file in one code block; the driver writes it to the
expected path - this measures the SDK's own orchestration/prompting stack on
top of the backend, not a tool-using agent. Semantic Kernel's agent API is
async throughout, so each prompt is run via `asyncio.run`.
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


class SemanticKernelDriver(Driver):
    name = "semantic-kernel"

    def prepare(self, scenario: Scenario, workspace: Path) -> None:
        try:
            import semantic_kernel  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("semantic-kernel not installed - pip install semantic-kernel") from exc

    def run_case(self, case: dict, scenario: Scenario, workspace: Path) -> CaseResult:
        from openai import AsyncOpenAI
        from semantic_kernel.agents import ChatCompletionAgent
        from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion

        result = CaseResult(name=case["name"])
        backend = scenario.backend
        prepare_workspace(workspace, case)
        before = snapshot(workspace)

        expected = case.get("expected_files", [])
        target = concrete_target(expected[0]["path_pattern"] if expected else None)

        async def _run_prompts() -> None:
            # No base_url kwarg on OpenAIChatCompletion itself - point it at
            # the scenario backend via an explicit AsyncOpenAI client instead
            # of env vars, same reasoning as the other SDK drivers (never let
            # a scenario's key leak into/get shadowed by the process env).
            client = AsyncOpenAI(base_url=backend.openai_base, api_key=backend.api_key or "optarena")
            try:
                service = OpenAIChatCompletion(ai_model_id=backend.model, async_client=client)
                agent = ChatCompletionAgent(
                    service=service,
                    name="software_engineer",
                    instructions="You are a precise engineer who answers with one fenced code "
                                 "block containing the complete file content, and nothing else.",
                )
                thread = None
                for prompt in case.get("prompts", []):
                    context = ""
                    if (workspace / target).exists():
                        context = (
                            f"\nCurrent content of {target.name}:\n```\n"
                            f"{(workspace / target).read_text(encoding='utf-8', errors='replace')}\n```"
                        )
                    response = await agent.get_response(
                        messages=prompt + context +
                        "\nReply with exactly one fenced code block containing the full file.",
                        thread=thread,
                    )
                    thread = response.thread
                    out = str(response.content)
                    blocks = _CODE_BLOCK.findall(out)
                    content = blocks[0].strip() + "\n" if blocks else out.strip() + "\n"
                    dest = workspace / target
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(content, encoding="utf-8")
            finally:
                # Without an explicit close, the AsyncOpenAI client's httpx
                # connections get garbage-collected AFTER asyncio.run() has
                # already torn down the event loop, raising a noisy (and, if
                # it happens badly enough, result-losing) "Event loop is
                # closed" RuntimeError on interpreter exit - same fix as the
                # autogen driver's client.close().
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
