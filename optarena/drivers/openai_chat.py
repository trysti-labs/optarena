"""
optarena/drivers/openai_chat.py
────────────────────────────
Raw-model baseline drivers (no agent). The model is asked to produce the file
content directly; the driver extracts the code block and writes it to the
expected path itself. Comparing an agent tool against this baseline quantifies
what the *tool* adds (multi-step planning, file ops, retries) beyond the model.

Two variants share the implementation:
  OpenAIChatDriver  → POST {base}/v1/chat/completions   (any OpenAI-compat API)
  OllamaChatDriver  → POST {base}/api/chat              (Ollama-native)
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

from ..cases import (
    apply_disruptions, changed_files, check_expected, evaluate_case,
    evaluate_case_isolated, prepare_workspace, snapshot,
)
from ..pricing import estimate_cost
from ..scenario import Scenario
from .base import CaseResult, Driver

_CODE_BLOCK = re.compile(r"```(?:\w+[^\n]*)?\n(.*?)```", re.DOTALL)

_SYSTEM = (
    "You are a code generator. Reply with exactly ONE fenced code block containing "
    "the complete file content requested - no commentary outside the block."
)


def concrete_target(pattern: str | None) -> Path:
    """
    Turn an expected-file `path_pattern` into a concrete path a baseline
    driver can write to. Patterns are globs ("**/HealthController.java",
    "*_test.go") - writing them literally creates directories named `**` on
    POSIX and crashes outright on Windows (`*` is invalid in filenames).
    Glob-bearing directory parts are dropped and glob metacharacters in the
    basename become "output" ("*_test.go" -> "output_test.go", "*.tf" ->
    "output.tf"), which still satisfies the basename glob match.
    """
    if not pattern:
        return Path("output.txt")
    parts = Path(pattern).parts
    dirs = [p for p in parts[:-1] if not any(ch in p for ch in "*?[]")]
    name = re.sub(r"[*?\[\]]+", "output", parts[-1]) if parts else "output.txt"
    return Path(*dirs, name)


def _post_json(url: str, payload: dict, timeout: int, headers: dict | None = None) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class OpenAIChatDriver(Driver):
    name = "openai-chat"
    parallel_safe = True   # stateless HTTP per case

    def _chat(self, prompt: str, scenario: Scenario, timeout: int) -> tuple[str, dict]:
        backend = scenario.backend
        body = _post_json(
            f"{backend.openai_base}/chat/completions",
            {
                "model": backend.model,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
            },
            timeout,
            headers={"Authorization": f"Bearer {backend.api_key}"},
        )
        text = body["choices"][0]["message"]["content"]
        usage = body.get("usage") or {}
        return text, usage

    def run_case(self, case: dict, scenario: Scenario, workspace: Path) -> CaseResult:
        result = CaseResult(name=case["name"])
        timeout = scenario.timeout or case.get("timeout", 120)
        prepare_workspace(workspace, case)
        before = snapshot(workspace)

        expected = case.get("expected_files", [])
        # The baseline writes files itself: target the first expected path per prompt.
        target = concrete_target(expected[0]["path_pattern"] if expected else None)

        steps: list[dict] = []
        n_prompts = len(case.get("prompts", []))
        fired_indices: set[int] = set()
        has_disruptions = bool(case.get("disruptions"))
        t0 = time.monotonic()
        try:
            for i, prompt in enumerate(case.get("prompts", []), 1):
                context = ""
                if (workspace / target).exists():
                    context = (
                        f"\n\nCurrent content of {target.name}:\n```\n"
                        f"{(workspace / target).read_text(encoding='utf-8', errors='replace')}\n```"
                    )
                s0 = time.monotonic()
                text, usage = self._chat(prompt + context, scenario, timeout)
                blocks = _CODE_BLOCK.findall(text)
                content = blocks[0].strip() + "\n" if blocks else text.strip() + "\n"
                dest = workspace / target
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    if key in usage:
                        result.extra[key] = result.extra.get(key, 0) + usage[key]
                # Per-step trajectory record: duration + tokens + whether the
                # model actually returned a fenced code block for this prompt
                # (no block => the driver fell back to writing raw text, a
                # weaker step even when the case still passes).
                step = {
                    "i": i, "duration_s": round(time.monotonic() - s0, 2),
                    "ok": bool(blocks),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                }
                # Tier 3: per-step expected-file status (before any disruption).
                step_files = changed_files(before, workspace)
                step["expected_ok"] = not check_expected(
                    step_files, case.get("expected_files", []), workspace)
                # Precise attribution: real oracle (expected files + check_command)
                # for every non-final prompt of a disruption case - see cli_agents.py
                # for why this is gated on has_disruptions, skips the final prompt,
                # and grades an isolated copy rather than the live workspace (C-01).
                if has_disruptions and i < n_prompts:
                    step_failures, _step_oracle = evaluate_case_isolated(case, step_files, workspace)
                    step["oracle_ok"] = not step_failures
                # Tier 1: fire disruptions whose trigger is satisfied at this
                # prompt boundary (fixed after_prompt or reactive `when`).
                fired = apply_disruptions(case, workspace, i, fired_indices)
                if fired:
                    step["disrupted"] = fired
                steps.append(step)
        except Exception as exc:  # noqa: BLE001 - report, don't crash the run
            result.error = f"{type(exc).__name__}: {exc}"
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
        result.passed = result.error is None and not result.failures
        if has_disruptions and steps:
            steps[-1]["oracle_ok"] = result.passed
        return result


class OllamaChatDriver(OpenAIChatDriver):
    name = "ollama-chat"

    def _chat(self, prompt: str, scenario: Scenario, timeout: int) -> tuple[str, dict]:
        backend = scenario.backend
        payload = {
            "model": backend.model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        if backend.num_ctx:
            # Only the native /api/chat honors this per-request - the
            # OpenAI-compat /v1/chat/completions path (every other driver)
            # silently ignores it on this Ollama version; see Backend.num_ctx.
            payload["options"] = {"num_ctx": backend.num_ctx}
        body = _post_json(
            f"{backend.base_url.rstrip('/')}/api/chat",
            payload,
            timeout,
        )
        text = body.get("message", {}).get("content", "")
        usage = {
            "prompt_tokens": body.get("prompt_eval_count", 0),
            "completion_tokens": body.get("eval_count", 0),
        }
        return text, usage
