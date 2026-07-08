"""
optarena/drivers/cli_agents.py
------------------------------
Headless terminal-agent drivers. One generic driver + a descriptor per tool:
everything tool-specific (binary name, prompt flags, auto-approval flag,
backend injection env) is data, mirroring how ui-harness/src/extensions.js
describes VS Code extensions.

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
import time
from pathlib import Path

from ..cases import changed_files, evaluate_case, snapshot, write_setup_files
from ..scenario import Scenario
from .base import CaseResult, Driver


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


def _scenario_openai_env(backend) -> dict:
    """Env for tools that read OpenAI-compatible endpoints from env vars."""
    return {
        "OPENAI_BASE_URL": backend.openai_base,
        "OPENAI_API_BASE": backend.openai_base,   # older tools read this name
        "OPENAI_API_KEY":  backend.api_key,
        "OPENAI_MODEL":    backend.model,
    }


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
        "argv":     lambda prompt, backend: ["run", prompt],
        "env":      _scenario_openai_env,
        "scrub_env_prefixes": (),
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
        write_setup_files(workspace, case.get("setup_files"))
        before = snapshot(workspace)

        env = {k: v for k, v in os.environ.items()
               if k != "ELECTRON_RUN_AS_NODE"
               and not k.startswith("VSCODE_")
               and not any(k.startswith(p) for p in self.spec["scrub_env_prefixes"])}
        env.update(self.spec["env"](scenario.backend))

        t0 = time.monotonic()
        try:
            for prompt in case.get("prompts", []):
                proc = subprocess.run(
                    [self._binary, *self.spec["argv"](prompt, scenario.backend)],
                    cwd=workspace, env=env,
                    capture_output=True, text=True, timeout=timeout,
                    encoding="utf-8", errors="replace",
                )
                parse = self.spec.get("parse_metrics")
                if parse:
                    for key, val in parse(proc.stdout).items():
                        result.extra[key] = result.extra.get(key, 0) + val
                if proc.returncode != 0:
                    result.extra.setdefault("stderr", "")
                    result.extra["stderr"] += (proc.stderr or "")[-800:]
        except subprocess.TimeoutExpired:
            result.error = f"{self.name} timed out after {timeout}s"
        except Exception as exc:  # noqa: BLE001 - report, don't crash the run
            result.error = f"{type(exc).__name__}: {exc}"
        result.duration_s = time.monotonic() - t0

        result.files = changed_files(before, workspace)
        result.failures, result.extra["oracle"] = evaluate_case(case, result.files, workspace)
        result.passed = result.error is None and not result.failures
        return result
