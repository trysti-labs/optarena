"""
optarena/drivers/aider_cli.py
──────────────────────────
Drives the aider CLI headlessly: one `aider --message "<prompt>"` invocation per
prompt, running inside the case workspace so aider's file edits land where the
oracle looks. Uses the OpenAI-compatible endpoint of the backend.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..cases import changed_files, evaluate_case, prepare_workspace, snapshot
from ..scenario import Scenario
from .base import CaseResult, Driver

# aider prints one line per message like:
#   Tokens: 4.5k sent, 431 received. Cost: $0.0042 message, $0.0084 session.
# (older versions: "Tokens: 8,975 sent, ..."; local backends omit Cost).
_TOKENS_RE = re.compile(
    r"Tokens:\s*([\d.,]+k?)\s*sent(?:[^.]*?([\d.,]+k?)\s*(?:received|returned))?",
    re.IGNORECASE)
_COST_RE = re.compile(r"\$([\d.]+)\s*session", re.IGNORECASE)


def _count(tok: str) -> int:
    tok = tok.replace(",", "").strip()
    if tok.lower().endswith("k"):
        return int(float(tok[:-1]) * 1000)
    return int(float(tok))


def parse_aider_metrics(stdout: str) -> dict:
    """Token/cost totals for ONE aider invocation, from its stdout report.

    Sums every per-message "Tokens:" line; takes the LAST "$X session" figure
    (aider's own running total for the invocation). Empty dict when aider
    printed no usage (quiet mode / unexpected format) - absent metrics must
    stay absent, not read as zero.
    """
    out: dict = {}
    sent = received = 0
    for m in _TOKENS_RE.finditer(stdout or ""):
        sent += _count(m.group(1))
        if m.group(2):
            received += _count(m.group(2))
    if sent or received:
        out["prompt_tokens"] = sent
        out["completion_tokens"] = received
    costs = _COST_RE.findall(stdout or "")
    if costs:
        out["cost_usd"] = float(costs[-1])
    return out


def find_aider() -> str | None:
    """Locate the aider binary (venv Scripts/bin first, then PATH)."""
    for candidate in (
        Path(sys.prefix) / "Scripts" / "aider.exe",
        Path(sys.prefix) / "bin" / "aider",
    ):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("aider")


class AiderDriver(Driver):
    name = "aider"
    parallel_safe = True   # one subprocess per case, isolated workspaces

    def __init__(self) -> None:
        self._aider: str | None = None

    def prepare(self, scenario: Scenario, workspace: Path) -> None:
        self._aider = find_aider()
        if not self._aider:
            raise RuntimeError(
                "aider not found. Install with: pip install aider-chat"
            )

    def run_case(self, case: dict, scenario: Scenario, workspace: Path) -> CaseResult:
        result = CaseResult(name=case["name"])
        timeout = scenario.timeout or case.get("timeout", 120)
        backend = scenario.backend
        prepare_workspace(workspace, case)
        before = snapshot(workspace)

        # Existing setup files must be added to aider's context so "modify" cases work.
        setup_names = list((case.get("setup_files") or {}).keys())

        env = {k: v for k, v in os.environ.items()
               if k != "ELECTRON_RUN_AS_NODE" and not k.startswith("VSCODE_")}

        t0 = time.monotonic()
        try:
            for prompt in case.get("prompts", []):
                cmd = [
                    self._aider,
                    "--openai-api-base", backend.openai_base,
                    "--openai-api-key", backend.api_key,
                    "--model", f"openai/{backend.model}",
                    "--no-git", "--yes", "--no-auto-commits",
                    "--no-show-model-warnings", "--no-check-update",
                    "--message", prompt,
                    *setup_names,
                ]
                proc = subprocess.run(
                    cmd, cwd=workspace, env=env,
                    capture_output=True, text=True, timeout=timeout,
                    encoding="utf-8", errors="replace",
                )
                for key, val in parse_aider_metrics(proc.stdout).items():
                    result.extra[key] = result.extra.get(key, 0) + val
                if proc.returncode != 0:
                    result.extra.setdefault("stderr", "")
                    result.extra["stderr"] += proc.stderr[-800:]
        except subprocess.TimeoutExpired:
            result.error = f"aider timed out after {timeout}s"
        except Exception as exc:  # noqa: BLE001
            result.error = f"{type(exc).__name__}: {exc}"
        result.duration_s = time.monotonic() - t0

        result.files = changed_files(before, workspace)
        result.failures, result.extra["oracle"] = evaluate_case(case, result.files, workspace)
        result.passed = result.error is None and not result.failures
        return result
