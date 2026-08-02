"""
optarena/drivers/autogen_sdk.py
------------------------------------
Optional driver: a minimal AutoGen/AG2 AssistantAgent (single agent, one run
per prompt) pointed at the backend's OpenAI-compatible endpoint. Requires
`pip install autogen-agentchat autogen-ext[openai]`.

The agent has no tools, so it is asked to output the complete file in one code
block; the driver writes it to the expected path - this measures the SDK's own
orchestration/prompting stack on top of the backend, not a tool-using agent.

AutoGen's chat client is async throughout, so this builds on
`sdk_base.AsyncSingleFileSDKDriver` (A-09): each prompt runs under
`asyncio.wait_for`, which gives the case deadline a real cancellation path
rather than the thread-abandonment the synchronous SDK drivers settle for.
"""

from __future__ import annotations

from ..scenario import Scenario
from .sdk_base import AsyncSingleFileSDKDriver

# AutoGen validates the target model against known OpenAI capability
# profiles unless told otherwise - a local/proxied model (Ollama, a router)
# isn't in that table, so model_info must be supplied explicitly. No tools
# are given to the agent, so function_calling/vision/json_output are all
# irrelevant to what this driver actually asks it to do.
_LOCAL_MODEL_INFO = {
    "vision": False, "function_calling": False,
    "json_output": False, "family": "unknown", "structured_output": False,
}


class AutoGenDriver(AsyncSingleFileSDKDriver):
    name = "autogen"
    import_names = ("autogen_agentchat", "autogen_ext.models.openai")
    install_hint = "pip install autogen-agentchat autogen-ext[openai]"

    async def aopen_session(self, scenario: Scenario):
        from autogen_agentchat.agents import AssistantAgent
        from autogen_ext.models.openai import OpenAIChatCompletionClient

        backend = scenario.backend
        client = OpenAIChatCompletionClient(
            model=backend.model,
            base_url=backend.openai_base,
            api_key=backend.api_key or "optarena",
            model_info=_LOCAL_MODEL_INFO,
        )
        agent = AssistantAgent(
            name="software_engineer",
            model_client=client,
            system_message="You are a precise engineer who answers with one fenced "
                            "code block containing the complete file content, and "
                            "nothing else.",
        )
        return (agent, client)

    async def aclose_session(self, session) -> None:
        # Without an explicit close, the client's httpx connections are
        # garbage-collected after the event loop is gone, raising a noisy
        # "Event loop is closed" at interpreter exit.
        _agent, client = session
        await client.close()

    async def acomplete(self, session, prompt: str, scenario: Scenario) -> "tuple[str, dict]":
        agent, _client = session
        task_result = await agent.run(task=prompt)
        return str(task_result.messages[-1].to_text()), _usage_from(task_result)


def _usage_from(task_result) -> dict:   # noqa: ANN001 - SDK-specific object
    """Sum `models_usage` across the result's messages, when reported.
    Empty dict otherwise - absent telemetry must not read as zero."""
    prompt_tokens = completion_tokens = 0
    seen = False
    for message in getattr(task_result, "messages", None) or []:
        usage = getattr(message, "models_usage", None)
        if usage is None:
            continue
        seen = True
        prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens += getattr(usage, "completion_tokens", 0) or 0
    if not seen:
        return {}
    return {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
