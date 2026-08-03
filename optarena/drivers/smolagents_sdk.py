"""
optarena/drivers/smolagents_sdk.py
------------------------------------
Optional driver: a minimal HuggingFace smolagents agent (single agent, one
run per prompt) pointed at the backend's OpenAI-compatible endpoint. Requires
`pip install smolagents`.

Uses `ToolCallingAgent` with no tools (not `CodeAgent`, which executes
model-written Python in this process - out of scope for a single-shot "write
one file" driver). The agent is asked to output the complete file in one code
block; the driver writes it to the expected path - this measures the SDK's own
orchestration/prompting stack on top of the backend, not a tool-using agent.

A-09: the case loop lives in `sdk_base.SingleFileSDKDriver`.
"""

from __future__ import annotations

from ..scenario import Scenario
from .sdk_base import SingleFileSDKDriver


class SmolAgentsDriver(SingleFileSDKDriver):
    name = "smolagents"
    import_names = ("smolagents",)
    install_hint = "pip install smolagents"

    def open_session(self, scenario: Scenario):
        from smolagents import OpenAIServerModel

        backend = scenario.backend
        # P2-02: generation parameters - confirmed live in OpenAIServerModel's
        # own docstring that **kwargs (e.g. temperature) forward straight
        # into the underlying OpenAI completion call; seed does too, same path.
        gen_kwargs = {}
        if backend.temperature is not None:
            gen_kwargs["temperature"] = backend.temperature
        if backend.top_p is not None:
            gen_kwargs["top_p"] = backend.top_p
        if backend.seed is not None:
            gen_kwargs["seed"] = backend.seed
        return OpenAIServerModel(
            model_id=backend.model,
            api_base=backend.openai_base,
            api_key=backend.api_key or "optarena",
            **gen_kwargs,
        )

    def complete(self, session, prompt: str, scenario: Scenario,
                 timeout: float) -> "tuple[str, dict]":
        from smolagents import ToolCallingAgent

        # Fresh agent per prompt - no cross-prompt tool-call state to carry,
        # and reset=True on a shared agent would drop the multi-prompt file
        # context anyway (the driver re-supplies it as `Current content of`).
        agent = ToolCallingAgent(tools=[], model=session, max_tool_threads=1)
        out = agent.run(prompt, max_steps=4)
        return str(out), _usage_from(session)


def _usage_from(model) -> dict:   # noqa: ANN001 - smolagents-specific object
    """Token counts off the smolagents model object, when it tracks them.
    Empty dict when absent - absent telemetry stays absent."""
    monitor = getattr(model, "last_output_token_count", None)
    usage: dict = {}
    if isinstance(getattr(model, "last_input_token_count", None), int):
        usage["prompt_tokens"] = model.last_input_token_count
    if isinstance(monitor, int):
        usage["completion_tokens"] = monitor
    return usage
