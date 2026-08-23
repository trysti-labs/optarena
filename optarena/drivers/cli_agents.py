"""
optarena/drivers/cli_agents.py
------------------------------
Headless terminal-agent drivers. One generic driver + a descriptor per tool:
everything tool-specific (binary name, prompt flags, auto-approval flag,
backend injection env) is data.

The 2026 CLI agents all share the same shape - spawn in a workspace, pass a
prompt, exit when done - which is exactly what the filesystem oracle wants.

Backend modes:
- ``scenario`` - the tool can be pointed at the scenario backend via env
  (OpenAI/Ollama-compatible), so backend-vs-backend comparisons are valid.
- ``fixed``    - the tool talks to its own account/provider (Claude Code,
  Codex by default); tool-vs-tool comparisons are valid, backend-vs-backend
  are NOT. Marked in the registry so the dashboard and users stay honest.

All of these are ``experimental`` until validated on a real install: the
invocation shapes track each tool's documented headless mode.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from ..cases import (
    apply_disruptions, changed_files, check_expected, evaluate_case,
    evaluate_case_isolated, prepare_workspace, run_capture, snapshot,
)
from ..scenario import Scenario
from ..security import redact_secrets
from .base import CaseResult, Driver, subprocess_env


def parse_claude_json_metrics(stdout: str) -> dict:
    """
    Token/cost/turn metrics from `claude -p --output-format json`, whose
    stdout is one JSON result object with `usage`, `total_cost_usd` and
    `num_turns`. Scans lines from the end (permission-mode banners may
    precede the JSON). Empty dict when no result object is found - absent
    metrics stay absent rather than reading as zero.
    """
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("type") != "result" and "usage" not in data:
            continue
        out: dict = {}
        usage = data.get("usage") or {}
        if usage:
            out["prompt_tokens"] = (usage.get("input_tokens", 0)
                                    + usage.get("cache_creation_input_tokens", 0)
                                    + usage.get("cache_read_input_tokens", 0))
            out["completion_tokens"] = usage.get("output_tokens", 0)
            if usage.get("cache_read_input_tokens"):
                out["cache_read_tokens"] = usage["cache_read_input_tokens"]
        if data.get("total_cost_usd") is not None:
            out["cost_usd"] = float(data["total_cost_usd"])
        if data.get("num_turns"):
            out["turns"] = int(data["num_turns"])
        return out
    return {}


def parse_gemini_json_metrics(stdout: str) -> dict:
    """Token metrics from Gemini CLI's ``--output-format json`` result."""
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        models = (data.get("stats") or {}).get("models") or {}
        if not isinstance(models, dict):
            continue
        out: dict = {}
        for model in models.values():
            if not isinstance(model, dict):
                continue
            tokens = model.get("tokens") or {}
            if not isinstance(tokens, dict):
                continue
            out["prompt_tokens"] = out.get("prompt_tokens", 0) + tokens.get("prompt", 0)
            out["completion_tokens"] = out.get("completion_tokens", 0) + tokens.get("candidates", 0)
            out["cache_read_tokens"] = out.get("cache_read_tokens", 0) + tokens.get("cached", 0)
        if out:
            if not out["cache_read_tokens"]:
                out.pop("cache_read_tokens")
            return out
    return {}


def _scenario_openai_env(backend) -> dict:
    """Env for tools that read OpenAI-compatible endpoints from env vars."""
    return {
        "OPENAI_BASE_URL": backend.openai_base,
        "OPENAI_API_BASE": backend.openai_base,   # older tools read this name
        "OPENAI_API_KEY":  backend.api_key,
        "OPENAI_MODEL":    backend.model,
    }


# opencode's built-in "openai" provider ID is reserved for the real OpenAI
# model catalog - pointing OPENAI_BASE_URL at a local server and asking for
# an unlisted model ID (e.g. "openai/gemma4:12b") fails fast with
# ProviderModelNotFoundError rather than treating it as a custom endpoint. A
# local/proxied backend has to be registered as its own named provider via a
# config file (https://opencode.ai/docs/providers/); this ID is that
# provider's name, referenced from both `_opencode_env` (writes the config)
# and the "opencode" argv below (selects it via `-m <id>/<model>`).
_OPENCODE_PROVIDER_ID = "optarena-local"


def _opencode_env(backend) -> dict:
    """
    Writes a scratch opencode.json registering the scenario backend as a
    custom `@ai-sdk/openai-compatible` provider, and points OPENCODE_CONFIG
    at it - never the user's real ~/.config/opencode/opencode.json. A fresh
    file per call (not a shared cached path) because run_case can be invoked
    concurrently across cases (this driver is parallel_safe).
    """
    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            _OPENCODE_PROVIDER_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "OptArena backend",
                "options": {"baseURL": backend.openai_base, "apiKey": backend.api_key or "optarena"},
                "models": {backend.model: {}},
            },
        },
    }
    fd, path = tempfile.mkstemp(prefix="optarena-opencode-config-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(config, f)
    return {"OPENCODE_CONFIG": path}


CLI_AGENTS: dict[str, dict] = {
    "claude-code": {
        "label":    "Claude Code",
        "binaries": ["claude"],
        "backend":  "fixed",       # uses the logged-in Anthropic account
        # --output-format json: same run, but stdout carries usage tokens,
        # total_cost_usd and num_turns, so this driver reports real cost
        # alongside the baselines instead of duration-only.
        "argv":     lambda prompt, backend: [
            "-p", prompt, "--dangerously-skip-permissions",
            "--output-format", "json",
        ],
        "env":      lambda backend: {},
        # Nested runs from inside an editor terminal must not inherit the
        # parent session's context.
        "scrub_env_prefixes": ("CLAUDE",),
        # "fixed" backend: Claude Code auths against the user's own Anthropic
        # account, either via `claude login` (config file, already reachable
        # through HOME/USERPROFILE) or ANTHROPIC_API_KEY - forward it if set,
        # since the allowlist otherwise wouldn't hand it through anymore.
        "auth_env": ("ANTHROPIC_API_KEY",),
        "parse_metrics": parse_claude_json_metrics,
    },
    "codex": {
        "label":    "Codex CLI",
        "binaries": ["codex"],
        "backend":  "fixed",       # provider set via codex config, not env
        "argv":     lambda prompt, backend: [
            "exec", "--full-auto", "--skip-git-repo-check", prompt,
        ],
        "env":      lambda backend: {},
        "scrub_env_prefixes": (),
    },
    "opencode": {
        "label":    "OpenCode",
        "binaries": ["opencode"],
        "backend":  "scenario",
        "argv":     lambda prompt, backend: [
            "run", "-m", f"{_OPENCODE_PROVIDER_ID}/{backend.model}", prompt,
        ],
        "env":      _opencode_env,
        "scrub_env_prefixes": (),
        # OPENCODE_CONFIG's value is a temp file _opencode_env wrote the
        # backend's API key into - see the cleanup in run_case's finally.
        "temp_env_keys": ("OPENCODE_CONFIG",),
    },
    "goose": {
        "label":    "Goose",
        "binaries": ["goose"],
        "backend":  "scenario",
        "argv":     lambda prompt, backend: ["run", "-t", prompt],
        "env":      lambda backend: {
            "GOOSE_PROVIDER": "openai",
            "GOOSE_MODEL":    backend.model,
            "OPENAI_HOST":    backend.base_url,
            "OPENAI_API_KEY": backend.api_key,
        },
        "scrub_env_prefixes": (),
    },
    "qwen-code": {
        "label":    "Qwen Code",
        "binaries": ["qwen"],
        "backend":  "scenario",
        "argv":     lambda prompt, backend: ["-p", prompt, "--yolo"],
        "env":      _scenario_openai_env,
        "scrub_env_prefixes": (),
    },
    "gemini-cli": {
        "label":    "Gemini CLI",
        "binaries": ["gemini"],
        "backend":  "fixed",       # uses the logged-in Google account/provider
        "argv":     lambda prompt, backend: [
            "-p", prompt, "--output-format", "json",
            "--approval-mode", "yolo", "--model", backend.model,
        ],
        "env":      lambda backend: {},
        "scrub_env_prefixes": (),
        "auth_env": (
            "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT_ID", "GOOGLE_CLOUD_LOCATION",
        ),
        "parse_metrics": parse_gemini_json_metrics,
    },
}


class CLIAgentDriver(Driver):
    """Generic headless CLI-agent driver, specialized by a CLI_AGENTS entry."""

    parallel_safe = True   # one subprocess per case, isolated workspaces

    def __init__(self, key: str) -> None:
        if key not in CLI_AGENTS:
            raise KeyError(f"Unknown CLI agent {key!r}")
        self.key = key
        self.spec = CLI_AGENTS[key]
        self.name = key
        self._binary: str | None = None

    def prepare(self, scenario: Scenario, workspace: Path) -> None:
        for candidate in self.spec["binaries"]:
            found = shutil.which(candidate)
            if found:
                self._binary = found
                break
        if not self._binary:
            raise RuntimeError(
                f"{self.spec['label']} not found on PATH "
                f"(looked for: {', '.join(self.spec['binaries'])})"
            )
        if self.spec["backend"] == "fixed":
            print(f"  [{self.name}] NOTE: fixed-backend tool - it uses its own "
                  f"provider/account, not the scenario backend. Tool-vs-tool "
                  f"comparisons are valid; backend-vs-backend are not.")

    def run_case(self, case: dict, scenario: Scenario, workspace: Path) -> CaseResult:
        result = CaseResult(name=case["name"])
        timeout = scenario.timeout or case.get("timeout", 180)
        prepare_workspace(workspace, case)
        before = snapshot(workspace)

        # C-03: an explicit allowlist, not a filtered copy of the whole host
        # environment - this subprocess's entire job is to execute
        # model-generated commands, so it must not inherit unrelated host
        # secrets (CI tokens, other cloud credentials) just because they
        # happened to be set in the parent shell. scrub_env_prefixes still
        # applies on top, for the nested-editor-session case.
        env = subprocess_env(self.spec["env"](scenario.backend),
                             passthrough=self.spec.get("auth_env", ()))
        env = {k: v for k, v in env.items()
               if not any(k.startswith(p) for p in self.spec["scrub_env_prefixes"])}
        # P1-01: every credential this subprocess actually has access to -
        # the scenario's own backend key, plus whatever auth_env passthrough
        # keys resolved to. Redacted out of anything captured FROM the
        # subprocess below (stderr, exception text) before it's stored,
        # since a tool can echo an env var it was handed - intentionally
        # (a verbose auth error) or not (a crash dump, a debug log line).
        known_secrets = [scenario.backend.api_key] + [
            env.get(k) for k in self.spec.get("auth_env", ())]

        steps: list[dict] = []
        n_prompts = len(case.get("prompts", []))
        # Reactive (`when`) disruption triggers must fire exactly once even
        # though their condition can stay true across several prompt
        # boundaries - this set is owned by THIS run_case call (fresh per
        # case/trial, never shared) and threaded through every
        # apply_disruptions call below.
        fired_indices: set[int] = set()
        # Precise per-step attribution: for cases WITH disruptions (a small,
        # deliberate subset of the corpus), run the REAL oracle - not just the
        # cheap expected-file check - after each non-final prompt, so failure
        # attribution can say "the behavioral test passed after prompt 2,
        # failed after prompt 3" instead of only "expected files existed".
        # Gated on has_disruptions: running check_command after every prompt
        # of every case would multiply Docker exec calls for no benefit on the
        # vast majority of (non-dynamic) cases.
        has_disruptions = bool(case.get("disruptions"))
        t0 = time.monotonic()
        # F-05: ONE deadline for the whole case, not `timeout` handed out
        # fresh to every prompt - a 3-prompt case could otherwise consume
        # roughly 3x the configured budget (plus oracle time on top), which
        # is what `timeout` is documented to mean everywhere else (case-level,
        # not per-prompt).
        deadline = t0 + timeout
        try:
            for i, prompt in enumerate(case.get("prompts", []), 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result.error = (
                        f"case deadline ({timeout}s) exceeded before prompt {i}/{n_prompts}"
                    )
                    break
                # run_capture (not subprocess.run): on timeout it kills the
                # agent's whole process tree, not just the agent binary, so
                # anything it spawned (a language server, a shelled-out tool)
                # can't outlive the case (H-11). `timeout=remaining`, not the
                # case's full `timeout` - see the deadline comment above.
                s0 = time.monotonic()
                proc = run_capture(
                    [self._binary, *self.spec["argv"](prompt, scenario.backend)],
                    cwd=workspace, env=env, timeout=remaining,
                    text=True, encoding="utf-8", errors="replace",
                )
                # Per-step trajectory record ("judge the path"): one entry per
                # prompt/turn, so a run's step-by-step timing/tokens/exit is
                # inspectable, not just the case total.
                step: dict = {"i": i, "duration_s": round(time.monotonic() - s0, 2),
                              "ok": proc.returncode == 0, "exit_code": proc.returncode}
                parse = self.spec.get("parse_metrics")
                if parse:
                    metrics = parse(proc.stdout)
                    for key, val in metrics.items():
                        result.extra[key] = result.extra.get(key, 0) + val
                    for key in ("prompt_tokens", "completion_tokens", "turns"):
                        if key in metrics:
                            step[key] = metrics[key]
                # Tier 3 (failure attribution): cheap per-step snapshot of whether
                # the expected-file checks pass right now, measured BEFORE any
                # disruption fires - so we can later say "satisfied after prompt 2,
                # regressed by prompt 3".
                step_files = changed_files(before, workspace)
                step["expected_ok"] = not check_expected(
                    step_files, case.get("expected_files", []), workspace)
                # Precise attribution: the real oracle (expected files AND
                # check_command), for every prompt but the last (the last
                # prompt's real oracle result is the case's own final verdict,
                # computed once below - no need to run check_command twice).
                # evaluate_case_isolated (NOT evaluate_case): this call happens
                # mid-session, in the SAME workspace the next prompt's agent
                # invocation runs in - grading a private copy keeps the hidden
                # test_setup_files from ever being visible on disk to the agent.
                if has_disruptions and i < n_prompts:
                    step_failures, _step_oracle = evaluate_case_isolated(case, step_files, workspace)
                    step["oracle_ok"] = not step_failures
                # Tier 1 (dynamic eval): fire any disruption whose trigger is
                # satisfied at this prompt boundary, so the NEXT prompt runs in
                # the changed environment.
                fired = apply_disruptions(case, workspace, i, fired_indices)
                if fired:
                    step["disrupted"] = fired
                steps.append(step)
                if proc.returncode != 0:
                    # execution_ok=False (H-XX / Phase 2.7): the tool itself
                    # reported failure. Previously this only went into
                    # `extra["stderr"]` for a human to notice - `passed` below
                    # depended solely on whatever the oracle found, so a
                    # non-zero exit on (say) the final prompt couldn't stop an
                    # already-passing artifact from an earlier prompt (or a
                    # lucky partial write) from being graded an unqualified
                    # PASS.
                    result.execution_ok = False
                    result.extra.setdefault("stderr", "")
                    result.extra["stderr"] += redact_secrets(
                        (proc.stderr or "")[-800:], known_secrets)
        except subprocess.TimeoutExpired:
            result.error = f"{self.name} timed out after {timeout}s"
        except Exception as exc:  # noqa: BLE001 - report, don't crash the run
            # P1-01: an HTTP client's own exception text can embed a
            # credential (a URL query param, a header dump) - redact the
            # same way stderr above is.
            result.error = redact_secrets(f"{type(exc).__name__}: {exc}", known_secrets)
        finally:
            # A driver's env-builder can create a scratch file (e.g.
            # opencode's per-run config carrying the backend's API key) -
            # temp_env_keys names the env-dict keys whose values are such
            # paths, so they get deleted regardless of success/timeout/
            # exception rather than accumulating indefinitely in the OS
            # temp dir with credentials still readable inside them.
            for _key in self.spec.get("temp_env_keys", ()):
                _path = env.get(_key)
                if _path:
                    Path(_path).unlink(missing_ok=True)
        result.duration_s = time.monotonic() - t0
        if steps:
            result.extra["steps"] = steps
            result.extra["n_steps"] = len(steps)

        result.files = changed_files(before, workspace)
        result.failures, result.extra["oracle"] = evaluate_case(case, result.files, workspace)
        result.passed = result.execution_ok and result.error is None and not result.failures
        if has_disruptions and steps:
            # The final prompt's real-oracle verdict IS the case verdict -
            # reuse it rather than re-running check_command a second time.
            steps[-1]["oracle_ok"] = result.passed
        return result
