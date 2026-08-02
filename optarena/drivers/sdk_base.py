"""
optarena/drivers/sdk_base.py
────────────────────────────
Shared implementation for the in-process SDK/agent-framework drivers
(crewAI, OpenAI Agents SDK, smolagents, LangGraph, AutoGen, Semantic Kernel).

A-09: these six drivers were six near-identical copies of the same ~50-line
loop, and every one of them was missing what the baseline and CLI drivers do:

- **no timeout of any kind** - `scenario.timeout` / `case["timeout"]` were
  never read, so a backend that accepts the connection and never answers hung
  the whole run with nothing to interrupt it. The corpus declares per-case
  timeouts up to 900s that simply did not apply to these drivers.
- **no disruptions** - `apply_disruptions` was never called, so the dynamic
  (`*_dynamic`) cases silently graded an EASIER task: the agent never faced
  the mid-session environment change the case exists to measure, while
  `compare`/`regression` still ranked the result against a CLI driver that
  did face it.
- **no per-step records, tokens, or cost** - so `tokens_per_pass`,
  `steps_per_pass`, `total_cost_usd` and failure attribution were all empty
  for SDK runs, and `cheaper` degraded to "unknown".

Everything above now lives here once, so a driver file is just "how do I ask
this SDK for one completion". Subclasses implement `complete()` (sync) or
subclass `AsyncSingleFileSDKDriver` and implement `acomplete()`.

Like the raw-model baselines, these agents get no file tools: they are asked
for one fenced code block and the driver writes it to the case's first
expected path (see `README.md`'s "Baseline caveat").
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import re
import time
from pathlib import Path

from ..cases import (
    apply_disruptions, changed_files, check_expected, evaluate_case,
    evaluate_case_isolated, prepare_workspace, snapshot,
)
from ..pricing import estimate_cost
from ..scenario import Scenario
from .base import CaseResult, Driver
from .openai_chat import concrete_target

_CODE_BLOCK = re.compile(r"```(?:\w+[^\n]*)?\n(.*?)```", re.DOTALL)

#: Appended to every prompt: these agents have no file tools, so the single
#: fenced block IS the deliverable.
ONE_BLOCK_INSTRUCTION = (
    "\nReply with exactly one fenced code block containing the full file."
)


class SDKTimeout(Exception):
    """A completion did not return within the case's remaining budget."""


class SingleFileSDKDriver(Driver):
    """
    Base for the "ask the SDK for one file, write it, grade the workspace"
    drivers. Subclasses provide:

    - ``import_names`` / ``install_hint`` - what ``prepare()`` checks for.
    - ``configure_environment()`` - optional, runs BEFORE the import check
      (telemetry/tracing opt-outs have to be set before the SDK is imported).
    - ``open_session(scenario)`` / ``close_session(session)`` - optional
      per-case client/agent lifecycle.
    - ``complete(session, prompt, scenario, timeout)`` -> ``(text, usage)``.
    """

    #: Import names that must resolve for this driver to run.
    import_names: tuple[str, ...] = ()
    #: Shown when they don't, e.g. "pip install crewai".
    install_hint: str = ""

    # Left False deliberately. These frameworks run IN-PROCESS and share
    # module-level state (client registries, tracing singletons, event loops);
    # unlike the CLI drivers - one isolated subprocess per case - their thread
    # safety is a property of each third-party framework, not of this harness,
    # and is not something this project has verified for any of them.
    parallel_safe = False

    # ── subclass hooks ────────────────────────────────────────────────────
    def configure_environment(self) -> None:
        """Runs before the SDK is imported (telemetry opt-outs, env pinning)."""

    def open_session(self, scenario: Scenario):   # noqa: ANN201 - SDK-specific object
        """Optional per-case client/agent. Whatever this returns is passed to
        every ``complete()`` call for that case and then to ``close_session``."""
        return None

    def close_session(self, session) -> None:     # noqa: ANN001 - SDK-specific object
        """Release whatever ``open_session`` created (HTTP clients, loops)."""

    def complete(self, session, prompt: str, scenario: Scenario,
                 timeout: float) -> "tuple[str, dict]":
        """One completion. Returns ``(text, usage)``; ``usage`` may be empty
        (``{}``) when the SDK does not report token counts - absent telemetry
        must stay absent rather than read as zero."""
        raise NotImplementedError

    # ── lifecycle ─────────────────────────────────────────────────────────
    def prepare(self, scenario: Scenario, workspace: Path) -> None:
        self.configure_environment()
        import importlib
        for name in self.import_names:
            try:
                importlib.import_module(name)
            except ImportError as exc:
                raise RuntimeError(
                    f"{self.name} not installed - {self.install_hint}") from exc

    # ── deadline enforcement ──────────────────────────────────────────────
    def _complete_with_deadline(self, session, prompt: str, scenario: Scenario,
                                 remaining: float) -> "tuple[str, dict]":
        """
        A-09: run one completion under the case's REMAINING budget.

        These SDKs are synchronous, in-process calls with no cancellation
        token, so the call is made on a worker thread and abandoned if it
        overruns. Abandoning is not killing: the orphaned call keeps running
        until the SDK's own HTTP timeout expires (the thread is a daemon, so
        it can never block interpreter exit). That is the honest ceiling for
        an in-process driver - what matters is that the RUN moves on and
        reports a timeout instead of hanging forever, which is what it used
        to do. `AsyncSingleFileSDKDriver` below does cancel properly, since
        asyncio gives it a real cancellation path.
        """
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"optarena-{self.name}")
        try:
            future = executor.submit(self.complete, session, prompt, scenario, remaining)
            try:
                return future.result(timeout=remaining)
            except concurrent.futures.TimeoutError:
                future.cancel()
                raise SDKTimeout(
                    f"{self.name} did not respond within the case's remaining "
                    f"{remaining:.0f}s budget"
                ) from None
        finally:
            # wait=False: never block the run on an abandoned call.
            executor.shutdown(wait=False)

    # ── the shared case loop ──────────────────────────────────────────────
    def run_case(self, case: dict, scenario: Scenario, workspace: Path) -> CaseResult:
        result = CaseResult(name=case["name"])
        timeout = scenario.timeout or case.get("timeout", 120)
        prepare_workspace(workspace, case)
        before = snapshot(workspace)

        expected = case.get("expected_files", [])
        target = concrete_target(expected[0]["path_pattern"] if expected else None)

        steps: list[dict] = []
        prompts = case.get("prompts", [])
        n_prompts = len(prompts)
        # Reactive (`when`) disruption triggers must fire exactly once even
        # though their condition can stay true across later prompt boundaries.
        # Owned by THIS call (fresh per case/trial, never shared).
        fired_indices: set[int] = set()
        has_disruptions = bool(case.get("disruptions"))

        t0 = time.monotonic()
        # F-05: ONE deadline for the whole case, not a fresh budget per
        # prompt - matching what `timeout` means for every other driver.
        deadline = t0 + timeout
        session = None
        try:
            session = self.open_session(scenario)
            for i, prompt in enumerate(prompts, 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result.error = (
                        f"case deadline ({timeout}s) exceeded before prompt {i}/{n_prompts}")
                    break
                context = ""
                if (workspace / target).exists():
                    context = (
                        f"\nCurrent content of {target.name}:\n```\n"
                        f"{(workspace / target).read_text(encoding='utf-8', errors='replace')}\n```"
                    )
                s0 = time.monotonic()
                text, usage = self._complete_with_deadline(
                    session, prompt + context + ONE_BLOCK_INSTRUCTION, scenario, remaining)
                blocks = _CODE_BLOCK.findall(text)
                content = blocks[0].strip() + "\n" if blocks else (text or "").strip() + "\n"
                dest = workspace / target
                dest.parent.mkdir(parents=True, exist_ok=True)
                # newline="" disables universal-newline translation - same
                # Windows CRLF-corruption fix as openai_chat.py/
                # cases.write_setup_files.
                dest.write_text(content, encoding="utf-8", newline="")

                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    if usage.get(key) is not None:
                        result.extra[key] = result.extra.get(key, 0) + usage[key]

                # Per-step trajectory record, same shape the baseline records:
                # `ok` is "did the model actually return a fenced block" (no
                # block means the driver fell back to writing raw text - a
                # weaker step even when the case still passes).
                step = {
                    "i": i, "duration_s": round(time.monotonic() - s0, 2),
                    "ok": bool(blocks),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                }
                step_files = changed_files(before, workspace)
                step["expected_ok"] = not check_expected(
                    step_files, case.get("expected_files", []), workspace)
                # Precise attribution for disruption cases only - grading an
                # isolated copy (C-01), never the live workspace.
                if has_disruptions and i < n_prompts:
                    step_failures, _ = evaluate_case_isolated(case, step_files, workspace)
                    step["oracle_ok"] = not step_failures
                # Tier 1: fire whatever this prompt boundary triggers, so the
                # NEXT prompt runs in the changed environment.
                fired = apply_disruptions(case, workspace, i, fired_indices)
                if fired:
                    step["disrupted"] = fired
                steps.append(step)
        except SDKTimeout as exc:
            result.error = str(exc)
            result.execution_ok = False
        except Exception as exc:  # noqa: BLE001 - report, don't crash the run
            result.error = f"{type(exc).__name__}: {exc}"
        finally:
            if session is not None:
                try:
                    self.close_session(session)
                except Exception:      # noqa: BLE001 - cleanup is best-effort
                    pass

        result.duration_s = time.monotonic() - t0
        if steps:
            result.extra["steps"] = steps
            result.extra["n_steps"] = len(steps)

        # USD cost from token usage (0 for local backends / unpriced models).
        result.extra["cost_usd"] = estimate_cost(
            scenario.backend.model,
            result.extra.get("prompt_tokens", 0),
            result.extra.get("completion_tokens", 0),
            base_url=scenario.backend.base_url,
        )

        result.files = changed_files(before, workspace)
        result.failures, result.extra["oracle"] = evaluate_case(case, result.files, workspace)
        result.passed = result.execution_ok and result.error is None and not result.failures
        if has_disruptions and steps:
            steps[-1]["oracle_ok"] = result.passed
        return result


class AsyncSingleFileSDKDriver(SingleFileSDKDriver):
    """
    For SDKs whose agent API is async throughout (AutoGen, Semantic Kernel).

    Each prompt runs through ``asyncio.run(asyncio.wait_for(...))``, so a
    timeout is a REAL cancellation of the pending coroutine rather than the
    thread-abandonment the sync path has to settle for. The per-case client
    lifecycle is handled inside the same event loop (an httpx client created
    in one loop cannot be closed from another), which is why `open_session`
    is not used here: `acomplete` receives the session it creates per call
    via `aopen_session`/`aclose_session`.
    """

    def open_session(self, scenario: Scenario):
        return None

    async def aopen_session(self, scenario: Scenario):   # noqa: ANN201
        """Async counterpart of `open_session`, created inside the loop."""
        return None

    async def aclose_session(self, session) -> None:     # noqa: ANN001
        """Async counterpart of `close_session`."""

    async def acomplete(self, session, prompt: str, scenario: Scenario) -> "tuple[str, dict]":
        raise NotImplementedError

    def complete(self, session, prompt: str, scenario: Scenario,
                 timeout: float) -> "tuple[str, dict]":
        # Not used - _complete_with_deadline is overridden below.
        raise NotImplementedError

    def _complete_with_deadline(self, session, prompt: str, scenario: Scenario,
                                 remaining: float) -> "tuple[str, dict]":
        async def _run() -> "tuple[str, dict]":
            sess = await self.aopen_session(scenario)
            try:
                return await asyncio.wait_for(
                    self.acomplete(sess, prompt, scenario), timeout=remaining)
            finally:
                # Closed in the loop that created it, and closed even when
                # wait_for cancelled the call - otherwise the client's httpx
                # connections are collected after the loop is gone, raising
                # "Event loop is closed" at interpreter exit.
                try:
                    await self.aclose_session(sess)
                except Exception:      # noqa: BLE001 - cleanup is best-effort
                    pass

        try:
            return asyncio.run(_run())
        except asyncio.TimeoutError:
            raise SDKTimeout(
                f"{self.name} did not respond within the case's remaining "
                f"{remaining:.0f}s budget"
            ) from None
