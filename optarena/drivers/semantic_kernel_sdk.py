"""
optarena/drivers/semantic_kernel_sdk.py
------------------------------------------
Optional driver: a minimal Semantic Kernel ChatCompletionAgent (single
agent, one run per prompt) pointed at the backend's OpenAI-compatible
endpoint. Requires `pip install semantic-kernel`.

The agent has no plugins/tools, so it is asked to output the complete file in
one code block; the driver writes it to the expected path - this measures the
SDK's own orchestration/prompting stack on top of the backend.

Semantic Kernel's agent API is async throughout, so this builds on
`sdk_base.AsyncSingleFileSDKDriver` (A-09) and gets real `asyncio.wait_for`
cancellation on the case deadline.
"""

from __future__ import annotations

from ..scenario import Scenario
from .sdk_base import AsyncSingleFileSDKDriver


class SemanticKernelDriver(AsyncSingleFileSDKDriver):
    name = "semantic-kernel"
    import_names = ("semantic_kernel",)
    install_hint = "pip install semantic-kernel"

    async def aopen_session(self, scenario: Scenario):
        from openai import AsyncOpenAI
        from semantic_kernel.agents import ChatCompletionAgent
        from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion

        backend = scenario.backend
        # No base_url kwarg on OpenAIChatCompletion itself - point it at the
        # scenario backend via an explicit AsyncOpenAI client instead of env
        # vars, same reasoning as the other SDK drivers (never let a
        # scenario's key leak into, or get shadowed by, the process env).
        client = AsyncOpenAI(base_url=backend.openai_base,
                             api_key=backend.api_key or "optarena")
        agent = ChatCompletionAgent(
            service=OpenAIChatCompletion(ai_model_id=backend.model, async_client=client),
            name="software_engineer",
            instructions="You are a precise engineer who answers with one fenced code "
                         "block containing the complete file content, and nothing else.",
        )
        # `thread` carries the conversation across this case's prompts.
        return {"agent": agent, "client": client, "thread": None}

    async def aclose_session(self, session) -> None:
        # Without an explicit close, the AsyncOpenAI client's httpx
        # connections get garbage-collected AFTER the event loop has been torn
        # down, raising a noisy (and, if it happens badly enough,
        # result-losing) "Event loop is closed" RuntimeError at exit.
        await session["client"].close()

    async def acomplete(self, session, prompt: str, scenario: Scenario) -> "tuple[str, dict]":
        response = await session["agent"].get_response(
            messages=prompt, thread=session["thread"])
        session["thread"] = response.thread
        return str(response.content), _usage_from(response)


def _usage_from(response) -> dict:   # noqa: ANN001 - SDK-specific object
    """Token usage off the response metadata, when Semantic Kernel reports it.
    Empty dict otherwise."""
    metadata = getattr(getattr(response, "content", None), "metadata", None) or {}
    usage = metadata.get("usage") if isinstance(metadata, dict) else None
    if usage is None:
        return {}
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    out: dict = {}
    if prompt_tokens is not None:
        out["prompt_tokens"] = int(prompt_tokens)
    if completion_tokens is not None:
        out["completion_tokens"] = int(completion_tokens)
    return out
