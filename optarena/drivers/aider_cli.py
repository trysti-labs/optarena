"""
optarena/drivers/aider_cli.py
──────────────────────────
Drives the aider CLI headlessly: one `aider --message "<prompt>"` invocation per
prompt, running inside the case workspace so aider's file edits land where the
oracle looks. Uses the OpenAI-compatible endpoint of the backend.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..cases import changed_files, evaluate_case, prepare_workspace, run_capture, snapshot
from ..scenario import Scenario
from ..security import redact_secrets
from .base import CaseResult, Driver, subprocess_env

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

        # Existing files must be added to aider's context so "modify" cases
        # work - and, critically, so does everything setup_repo copied in.
        # `--no-git` below means aider has no repo map of its own to fall
        # back on, so a case's `setup_repo` files (the whole point of a
        # repo-scale case) were previously invisible to it entirely: only
        # `case["setup_files"]`'s own small overlay was ever passed. `before`
        # is the post-prepare_workspace snapshot (setup_repo + setup_files
        # both already applied, hidden test_setup_files not written until
        # after the agent's turn), so its keys are exactly "every file that
        # legitimately exists before the model starts editing".
        setup_names = sorted(before.keys())

        # C-03: an explicit allowlist, not a filtered copy of the whole host
        # environment - aider's job is to execute model-generated edits, so
        # it must not inherit unrelated host secrets just because they
        # happened to be set in the parent shell.
        #
        # A-06: the backend's API key goes in the ENVIRONMENT, not in argv.
        # Process arguments are readable by any local user (/proc/<pid>/cmdline
        # on Linux, any session on Windows), so `--openai-api-key <secret>`
        # leaked the scenario's key to every other user on the machine for the
        # lifetime of the subprocess. This driver was the only place doing
        # that, while cli.py's own --api-key help and SECURITY.md both tell
        # users to prefer the environment for exactly this reason. aider reads
        # OPENAI_API_KEY/OPENAI_API_BASE natively.
        env = subprocess_env({
            "OPENAI_API_KEY": backend.api_key,
            "OPENAI_API_BASE": backend.openai_base,
            "OPENAI_BASE_URL": backend.openai_base,
        })
        # P1-01: redacted out of anything captured FROM aider below (stderr,
        # exception text) before it's stored - aider can echo an env var it
        # was handed, intentionally (a verbose auth error) or not.
        known_secrets = [backend.api_key]

        t0 = time.monotonic()
        n_prompts = len(case.get("prompts", []))
        # F-05: ONE deadline for the whole case, not `timeout` handed out
        # fresh to every prompt - a multi-prompt case could otherwise consume
        # roughly N x the configured budget, which is what `timeout` is
        # documented to mean everywhere else (case-level, not per-prompt).
        deadline = t0 + timeout
        try:
            for i, prompt in enumerate(case.get("prompts", []), 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result.error = f"case deadline ({timeout}s) exceeded before prompt {i}/{n_prompts}"
                    break
                cmd = [
                    # A-06: no --openai-api-key/--openai-api-base here - both
                    # travel via `env` above so the key never appears in argv.
                    self._aider,
                    "--model", f"openai/{backend.model}",
                    "--no-git", "--yes", "--no-auto-commits",
                    "--no-show-model-warnings", "--no-check-update",
                    "--message", prompt,
                    *setup_names,
                ]
                # run_capture (not subprocess.run): on timeout it kills
                # aider's whole process tree, not just aider itself, so a
                # subprocess aider spawned can't outlive the case (H-11).
                # `timeout=remaining`, not the case's full `timeout` - see
                # the deadline comment above.
                proc = run_capture(
                    cmd, cwd=workspace, env=env, timeout=remaining,
                    text=True, encoding="utf-8", errors="replace",
                )
                for key, val in parse_aider_metrics(proc.stdout).items():
                    result.extra[key] = result.extra.get(key, 0) + val
                if proc.returncode != 0:
                    # execution_ok=False (Phase 2.7): aider itself reported
                    # failure - previously only logged to extra["stderr"],
                    # so a non-zero exit couldn't stop an already-passing
                    # artifact (an earlier prompt, or a lucky partial write)
                    # from being graded an unqualified PASS.
                    result.execution_ok = False
                    result.extra.setdefault("stderr", "")
                    result.extra["stderr"] += redact_secrets(proc.stderr[-800:], known_secrets)
        except subprocess.TimeoutExpired:
            result.error = f"aider timed out after {timeout}s"
        except Exception as exc:  # noqa: BLE001
            result.error = redact_secrets(f"{type(exc).__name__}: {exc}", known_secrets)
        result.duration_s = time.monotonic() - t0

        result.files = changed_files(before, workspace)
        result.failures, result.extra["oracle"] = evaluate_case(case, result.files, workspace)
        result.passed = result.execution_ok and result.error is None and not result.failures
        return result
