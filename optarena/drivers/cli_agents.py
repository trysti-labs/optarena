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

import os
import shutil
import subprocess
import time
from pathlib import Path

from ..cases import changed_files, evaluate_case, snapshot, write_setup_files
from ..scenario import Scenario
from .base import CaseResult, Driver


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
        "argv":     lambda prompt, backend: [
            "-p", prompt, "--dangerously-skip-permissions",
        ],
        "env":      lambda backend: {},
        # Nested runs from inside an editor terminal must not inherit the
        # parent session's context.
        "scrub_env_prefixes": ("CLAUDE",),
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
