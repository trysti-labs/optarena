"""
optarena/drivers/base.py
─────────────────────
Driver interface + CaseResult record.

A driver receives a case and a scratch workspace, runs the tool, and returns a
CaseResult. Drivers must be *stateless across cases* except via prepare()/
teardown() - a driver with expensive per-scenario startup could run every case
inside prepare() in one session and serve cached results from run_case() (see
caches_results below), rather than paying that startup cost per case.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..scenario import Scenario

# C-03: the bare minimum a CLI subprocess needs to find its binary, locate its
# own config/auth files, and (on Windows) be creatable at all - NOT the full
# host environment. `subprocess_env` below builds a driver subprocess's
# environment FROM this allowlist rather than filtering the host's, so an
# untrusted CLI agent (its entire job is executing model-generated commands)
# never inherits unrelated host secrets (CI tokens, other cloud credentials)
# just because they happened to be set in the parent shell.
_ENV_ALLOWLIST = (
    "PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "TERM",
    "TMPDIR", "TEMP", "TMP",
    "SystemRoot", "windir", "ComSpec", "PATHEXT", "SystemDrive",
)


def subprocess_env(extra: dict[str, str] | None = None,
                    passthrough: tuple[str, ...] = ()) -> dict[str, str]:
    """
    Build an explicit, minimal environment for spawning an untrusted CLI-agent
    subprocess. Starts from `_ENV_ALLOWLIST` (present-in-parent-env only),
    adds any `passthrough` names actually present in the parent environment
    (a "fixed"-backend tool's own real-world provider auth, e.g.
    ANTHROPIC_API_KEY, for users who authenticate that way rather than via
    interactive CLI login), then layers `extra` on top (the scenario
    backend's own env, e.g. OPENAI_API_KEY/OPENAI_BASE_URL) - `extra` always
    wins since it reflects what THIS run was explicitly configured to use.

    Matching is case-insensitive on purpose: Windows env var names are
    semantically case-insensitive, but the literal key `os.environ` hands
    back depends on whichever shell originally launched this process - Git
    Bash/MSYS2 exposes SYSTEMROOT/WINDIR/COMSPEC in all caps, not the
    SystemRoot/windir/ComSpec casing listed above. A plain `k in
    _ENV_ALLOWLIST` silently dropped them on that setup, and losing
    SystemRoot specifically hard-crashes any spawned Node process (its
    Windows CSPRNG/crypto init needs it to locate bcrypt.dll) - the exact
    failure that made every `optarena run --driver *-ui` (and any CLI driver
    shelling out to a Node-based tool) crash with `ncrypto::CSPRNG` on a
    Git-Bash-launched host, while running the same command by hand in a
    normal shell worked (full, unfiltered environment, casing intact).

    `SystemDrive` is in the allowlist for the same reason, but was missing
    outright rather than miscased: without it, part of the qwen-code CLI's
    Node toolchain fell back to the literal unexpanded string `%SystemDrive%`
    when building a cache-file path, then created that literal directory name
    relative to the subprocess's CWD - i.e. inside the sandboxed case
    workspace - which the oracle then reported as unexpected off-target file
    changes.
    """
    allow = {name.upper() for name in _ENV_ALLOWLIST}
    wanted = {name.upper() for name in passthrough}
    env = {k: v for k, v in os.environ.items() if k.upper() in allow}
    for k, v in os.environ.items():
        if k.upper() in wanted and k not in env:
            env[k] = v
    env.update(extra or {})
    return env


@dataclass
class CaseResult:
    name: str
    passed: bool = False
    duration_s: float = 0.0            # wall time for the case (tool work only)
    files: list[str] = field(default_factory=list)     # created/changed files
    failures: list[str] = field(default_factory=list)  # oracle failure strings
    error: str | None = None           # infrastructure error (≠ oracle failure)
    # False when the driver ran but the underlying tool itself reported
    # failure (non-zero exit, an in-band error field in its own JSON output)
    # WITHOUT raising - distinct from `error` (a Python-level exception/crash
    # in the driver) and from `failures` (the oracle's verdict on the
    # workspace). A tool can exit non-zero yet still leave behind a file that
    # happens to satisfy the oracle (an earlier prompt succeeded, or the
    # model got lucky); without this, that case reads as an unqualified PASS.
    execution_ok: bool = True
    extra: dict = field(default_factory=dict)          # driver-specific metrics
    # Copied from the case definition (not driver output) by the runner right
    # before a result is recorded, so a saved run can be filtered/grouped by
    # case metadata without re-reading optarena/cases/*.json. None rather than
    # "" when a case doesn't set the field, so absence stays distinguishable
    # from an empty string.
    language: str | None = None
    domain: str | None = None
    task_type: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "duration_s": round(self.duration_s, 2),
            "files": self.files,
            "failures": self.failures,
            "error": self.error,
            "execution_ok": self.execution_ok,
            "extra": self.extra,
            "language": self.language,
            "domain": self.domain,
            "task_type": self.task_type,
        }


class Driver:
    """Base driver. Subclasses implement run_case(); prepare/teardown optional."""

    name = "base"
    # True when run_case() only spawns an isolated subprocess / HTTP call per
    # case, so cases may run concurrently (each in its own workspace).
    parallel_safe = False
    # True when prepare() executes ALL cases once and run_case() serves cached
    # results - for a driver with expensive per-scenario startup, where paying
    # that startup per case would dominate the measurement.
    #
    # A-18: a supported extension point for out-of-tree drivers, deliberately
    # kept even though no bundled driver sets it. The contract it defines is
    # load-bearing on the runner side (M-09/F-09: the runner hands the trial
    # count TO such a driver and runs its own loop once, while the manifest
    # still records the REQUESTED trial count so comparability is unaffected)
    # and is covered by CachingDriverTrialsPlumbingTests / ManifestTrialsTests.
    caches_results = False

    def prepare(self, scenario: Scenario, workspace: Path) -> None:
        """Called once before the first case (start servers, launch editors…)."""

    def run_case(self, case: dict, scenario: Scenario, workspace: Path) -> CaseResult:
        raise NotImplementedError

    def teardown(self) -> None:
        """Called once after the last case (kill editors, cleanup…)."""
