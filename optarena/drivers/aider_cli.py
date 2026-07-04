"""
optarena/drivers/aider_cli.py
──────────────────────────
Drives the aider CLI headlessly: one `aider --message "<prompt>"` invocation per
prompt, running inside the case workspace so aider's file edits land where the
oracle looks. Uses the OpenAI-compatible endpoint of the backend.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..cases import changed_files, evaluate_case, snapshot, write_setup_files
from ..scenario import Scenario
from .base import CaseResult, Driver


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
        write_setup_files(workspace, case.get("setup_files"))
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
