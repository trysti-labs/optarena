"""
optarena/drivers/vscode_ui.py
──────────────────────────
Drives real VS Code extension UIs (Cline / Roo Code / Continue / Kilo Code)
via the `ui-harness` WebdriverIO harness in ../../ui-harness. That harness launches an
isolated VS Code, seeds the extension's config for the scenario backend,
types prompts into the actual webview chat, auto-approves, and verifies the
workspace diff - the OptArena driver just orchestrates it and collects results.

Because launching VS Code per case would dominate timing, prepare() executes
the whole scenario's case set in ONE editor session; the harness appends one
JSON line per case to RESULTS_FILE, which run_case() then serves from memory.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from ..scenario import Scenario
from .base import CaseResult, Driver, subprocess_env

HARNESS_DIR = Path(__file__).resolve().parents[2] / "ui-harness"

# Maturity per extension (also documented in drivers/__init__.py).
_STATUS = {"cline": "stable", "roo": "experimental", "continue": "experimental", "kilo": "experimental"}


class VSCodeUIDriver(Driver):
    caches_results = True   # prepare() runs the whole case set in one session

    def __init__(self, ext: str) -> None:
        self.ext = ext
        self.name = f"{ext}-ui"
        self._results: dict[str, CaseResult] = {}
        # Set by the runner (M-09): repeat each case this many times inside the
        # harness for a majority verdict + stability signal. 1 = historical.
        self.trials = 1

    def prepare(self, scenario: Scenario, workspace: Path) -> None:
        if not (HARNESS_DIR / "package.json").exists():
            raise RuntimeError(f"ui-harness not found at {HARNESS_DIR}")
        if not (HARNESS_DIR / "node_modules").exists():
            raise RuntimeError(
                f"ui-harness not installed - run: cd {HARNESS_DIR} && npm install"
            )
        if _STATUS.get(self.ext) != "stable":
            print(f"  [{self.name}] NOTE: this driver is {_STATUS.get(self.ext)}")

        # mkstemp returns an OPEN fd - close it immediately, or Windows blocks
        # the later unlink (WinError 32) while we still hold the handle.
        fd, tmp_name = tempfile.mkstemp(suffix=".jsonl", prefix="optarena_ui_")
        os.close(fd)
        results_file = Path(tmp_name)
        backend = scenario.backend

        # C-03: an explicit allowlist, like the CLI drivers - NOT a filtered
        # copy of the whole host environment. This env reaches npm -> wdio ->
        # VS Code -> the evaluated extension, so a stray host secret here is a
        # stray host secret inside the agent under test. Beyond the shared
        # base list, VS Code/Chromium need the display/session vars to launch
        # at all; wdio needs its own proxy/cache knobs when the user sets them.
        env = subprocess_env(passthrough=(
            # GUI/session (Linux X11/Wayland, macOS launchd, general)
            "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
            "XDG_SESSION_TYPE", "DBUS_SESSION_BUS_ADDRESS", "SHELL",
            "USER", "LOGNAME",
            # harness knobs (only forwarded when actually set)
            "EXT_PATH", "VSCODE_VERSION", "ONLY_CASE", "INTER_PROMPT_WAIT",
            "IDLE_MS", "OPTARENA_UI_DIR", "OPTARENA_NO_DOCKER",
            "OPTARENA_ALLOW_UNSAFE_HOST_EXEC", "OPTARENA_DOCKER_IMAGE",
            "OPTARENA_NO_PULL",
            # proxy settings npm/VS Code downloads may require
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "no_proxy",
        ))
        env.update({
            "EXT": self.ext,
            "BACKEND_URL": backend.base_url,
            "API_KIND": backend.kind,             # ollama | openai
            "MODEL_ID": backend.model,
            # H-08: forward the configured key so the extensions can auth
            # against a real (non-local) OpenAI-compatible backend. Was never
            # passed before, so the JS harness hardcoded "optarena" - fine for
            # Ollama, but any keyed endpoint (OpenRouter, a hosted vLLM behind
            # auth) silently got the wrong credential and 401'd.
            "API_KEY": backend.api_key,
            "RESULTS_FILE": str(results_file),
            "CASES_DIR": str(scenario.cases_dir
                              or Path(__file__).resolve().parents[1] / "cases"),
        })
        if scenario.cases:
            env["CASES"] = ",".join(scenario.cases)
        if scenario.timeout:
            env["CASE_TIMEOUT"] = str(scenario.timeout)
        if self.trials > 1:
            env["TRIALS"] = str(self.trials)

        npm = "npm.cmd" if os.name == "nt" else "npm"
        # Bound the whole harness run: per-case budget x trials + launch slack,
        # never less than 30 min (a cold first run also downloads VS Code,
        # ~280 MB).
        from ..cases import load_cases
        n_cases = len(load_cases(scenario.cases, cases_dir=scenario.cases_dir))
        run_timeout = max(60 * 30,
                          n_cases * self.trials * ((scenario.timeout or 150) + 120) + 600)
        print(f"  [{self.name}] launching VS Code UI run (this takes minutes)...")
        proc = subprocess.run(
            [npm, "test"], cwd=HARNESS_DIR, env=env,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",   # npm output is UTF-8, not cp1252
            timeout=run_timeout,
        )
        if results_file.exists():
            # The harness emits one JSONL line per (case, trial). Group by case
            # name and, when a case ran more than once, merge into the same
            # majority-verdict + per-trial CaseResult the CLI drivers produce
            # (M-09) - reusing the runner's _merge_trials keeps UI and CLI
            # stability metrics identical.
            from ..runner import _merge_trials
            per_case: dict[str, list[CaseResult]] = {}
            for line in results_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                per_case.setdefault(rec["name"], []).append(CaseResult(
                    name=rec["name"],
                    passed=rec.get("passed", False),
                    duration_s=rec.get("duration_s", 0.0),
                    files=rec.get("files", []),
                    failures=rec.get("failures", []),
                    error=rec.get("error"),
                    extra=rec.get("extra", {}),
                ))
            for name, trials in per_case.items():
                self._results[name] = _merge_trials(name, trials)
            try:
                results_file.unlink(missing_ok=True)
            except OSError:
                pass  # stray handle on Windows - temp dir cleanup will get it
        if not self._results:
            tail = (proc.stdout or "")[-1200:] + (proc.stderr or "")[-400:]
            raise RuntimeError(
                f"{self.name}: harness produced no results (exit {proc.returncode}). "
                f"Output tail:\n{tail}"
            )

    def run_case(self, case: dict, scenario: Scenario, workspace: Path) -> CaseResult:
        rec = self._results.get(case["name"])
        if rec is None:
            return CaseResult(
                name=case["name"],
                error="case not executed by UI harness (crashed earlier?)",
            )
        return rec
