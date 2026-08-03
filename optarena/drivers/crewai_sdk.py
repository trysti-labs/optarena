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

A-09: the case loop (whole-case deadline, per-step records, disruptions, token
and cost telemetry) lives in `sdk_base.SingleFileSDKDriver`; this file is just
"how do I ask crewAI for one completion".
"""

from __future__ import annotations

import os

from ..scenario import Scenario
from .sdk_base import SingleFileSDKDriver


class CrewAIDriver(SingleFileSDKDriver):
    name = "crewai"
    import_names = ("crewai",)
    install_hint = "pip install crewai"

    def configure_environment(self) -> None:
        # crewai.telemetry.Telemetry is a module-level singleton that reads
        # this env var once, at first construction (on import or first
        # Agent/Crew) - must be set before `import crewai`, or the singleton
        # latches "enabled" for the rest of the process and every run silently
        # phones home to crewAI's own telemetry endpoint, even for a fully
        # local/offline scenario. prepare() calls this before the import check.
        os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")

    def open_session(self, scenario: Scenario):
        from crewai import Agent, LLM

        backend = scenario.backend
        # The key is passed explicitly to LLM() - do NOT also write it into
        # os.environ: that leaked the scenario's key into this process's
        # environment for every later driver/subprocess of the run, and
        # conversely a pre-existing host OPENAI_API_KEY silently shadowed the
        # scenario's key for any crewAI internals reading the env.
        # P2-02: generation parameters - confirmed live that LLM (a pydantic
        # model over litellm) declares temperature/top_p/seed as real fields.
        kwargs = {}
        if backend.temperature is not None:
            kwargs["temperature"] = backend.temperature
        if backend.top_p is not None:
            kwargs["top_p"] = backend.top_p
        if backend.seed is not None:
            kwargs["seed"] = backend.seed
        llm = LLM(
            model=f"openai/{backend.model}",
            base_url=backend.openai_base,
            api_key=backend.api_key,
            **kwargs,
        )
        return Agent(
            role="Software Engineer",
            goal="Produce complete, working source files exactly as requested.",
            backstory="A precise engineer who answers with one fenced code block.",
            llm=llm, verbose=False,
        )

    def complete(self, session, prompt: str, scenario: Scenario,
                 timeout: float) -> "tuple[str, dict]":
        from crewai import Crew, Task

        task = Task(
            description=prompt,
            expected_output="One fenced code block with the complete file content.",
            agent=session,
        )
        crew = Crew(agents=[session], tasks=[task], verbose=False)
        output = crew.kickoff()
        return str(output), _usage_from(crew)


def _usage_from(crew) -> dict:   # noqa: ANN001 - crewAI-specific object
    """Token usage off crewAI's own `usage_metrics`, when it reports any.

    Returns {} rather than zeros when the metrics are absent - an unpriced or
    unreported run must not read as "0 tokens, $0.00"."""
    metrics = getattr(crew, "usage_metrics", None)
    if metrics is None:
        return {}
    get = (lambda k: getattr(metrics, k, None)) if not isinstance(metrics, dict) else metrics.get
    prompt_tokens, completion_tokens = get("prompt_tokens"), get("completion_tokens")
    usage: dict = {}
    if prompt_tokens is not None:
        usage["prompt_tokens"] = int(prompt_tokens)
    if completion_tokens is not None:
        usage["completion_tokens"] = int(completion_tokens)
    return usage
