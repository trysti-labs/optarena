"""
optarena/drivers/openai_agents_sdk.py
--------------------------------------
Optional driver: a minimal OpenAI Agents SDK agent (single agent, one run
per prompt) pointed at the backend's OpenAI-compatible endpoint. Exercises
the SDK-agent path rather than a CLI tool. Requires `pip install openai-agents`.

The agent has no file tools, so it is asked to output the complete file in one
code block; the driver writes it to the expected path - this measures the SDK's
own orchestration/prompting stack on top of the backend.

A-09: the case loop lives in `sdk_base.SingleFileSDKDriver`.
"""

from __future__ import annotations

import asyncio

from ..scenario import Scenario
from .sdk_base import SingleFileSDKDriver


class OpenAIAgentsDriver(SingleFileSDKDriver):
    name = "openai-agents"
    import_names = ("agents",)
    install_hint = "pip install openai-agents"

    def open_session(self, scenario: Scenario):
        from agents import Agent, OpenAIChatCompletionsModel, RunConfig
        from openai import AsyncOpenAI

        backend = scenario.backend
        # Explicit client, not env vars (OPENAI_API_KEY/OPENAI_BASE_URL) - a
        # real host key must never silently shadow (or be shadowed by) the
        # scenario's own backend, and nothing here should leak into the
        # process environment for later drivers in the same run.
        client = AsyncOpenAI(base_url=backend.openai_base,
                             api_key=backend.api_key or "optarena")
        agent = Agent(
            name="Software Engineer",
            instructions="You are a precise engineer who answers with one fenced code block "
                         "containing the complete file content, and nothing else.",
            model=OpenAIChatCompletionsModel(model=backend.model, openai_client=client),
        )
        # Tracing defaults to ON and, independently of the explicit client
        # above, reads the host's ambient OPENAI_API_KEY (not backend.api_key)
        # to export every prompt/completion to https://api.openai.com - a
        # scenario aimed at a local Ollama backend would otherwise silently
        # ship its content to OpenAI whenever that env var happens to be set.
        return (agent, RunConfig(tracing_disabled=True), client)

    def close_session(self, session) -> None:
        # A-41: found live during a driver smoke-test sweep - without this,
        # every case run through this driver printed 5 (one per case)
        # "Exception ignored in: <function _ProactorBasePipeTransport.__del__
        # ...> RuntimeError: Event loop is closed" tracebacks to stderr. Not
        # fatal (the run's actual pass/fail data and token telemetry were
        # unaffected), but noisy enough to look like a real crash under
        # `--debug`, and it means the client's httpx connections were never
        # actually released - the same class of leak `autogen_sdk.py` and
        # `semantic_kernel_sdk.py` already guard against with an explicit
        # client.close(). `AsyncOpenAI.close()` is itself a coroutine; this
        # driver's `complete()` is sync (`Runner.run_sync`), so it has to be
        # run through its own `asyncio.run()` here rather than awaited
        # directly - same fix, adapted to a sync driver.
        _agent, _run_config, client = session
        try:
            asyncio.run(client.close())
        except Exception:      # noqa: BLE001 - cleanup is best-effort
            pass

    def complete(self, session, prompt: str, scenario: Scenario,
                 timeout: float) -> "tuple[str, dict]":
        from agents import Runner

        agent, run_config, _client = session
        run_result = Runner.run_sync(agent, prompt, run_config=run_config)
        return str(run_result.final_output), _usage_from(run_result)


def _usage_from(run_result) -> dict:   # noqa: ANN001 - SDK-specific object
    """Token usage summed over the run's raw responses, when reported.
    Empty dict otherwise."""
    prompt_tokens = completion_tokens = 0
    seen = False
    for response in getattr(run_result, "raw_responses", None) or []:
        usage = getattr(response, "usage", None)
        if usage is None:
            continue
        seen = True
        prompt_tokens += getattr(usage, "input_tokens", 0) or 0
        completion_tokens += getattr(usage, "output_tokens", 0) or 0
    if not seen:
        return {}
    return {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
