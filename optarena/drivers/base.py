"""
optarena/drivers/base.py
─────────────────────
Driver interface + CaseResult record.

A driver receives a case and a scratch workspace, runs the tool, and returns a
CaseResult. Drivers must be *stateless across cases* except via prepare()/
teardown() (e.g. the VS Code UI driver keeps one editor session alive for a
whole scenario run because launching VS Code per case would dominate timing).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from ..scenario import Scenario


@dataclass
class CaseResult:
    name: str
    passed: bool = False
    duration_s: float = 0.0            # wall time for the case (tool work only)
    files: list[str] = field(default_factory=list)     # created/changed files
    failures: list[str] = field(default_factory=list)  # oracle failure strings
    error: str | None = None           # infrastructure error (≠ oracle failure)
    extra: dict = field(default_factory=dict)          # driver-specific metrics

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "duration_s": round(self.duration_s, 2),
            "files": self.files,
            "failures": self.failures,
            "error": self.error,
            "extra": self.extra,
        }


class Driver:
    """Base driver. Subclasses implement run_case(); prepare/teardown optional."""

    name = "base"

    def prepare(self, scenario: Scenario, workspace: Path) -> None:
        """Called once before the first case (start servers, launch editors…)."""

    def run_case(self, case: dict, scenario: Scenario, workspace: Path) -> CaseResult:
        raise NotImplementedError

    def teardown(self) -> None:
        """Called once after the last case (kill editors, cleanup…)."""

    # Convenience for subclasses.
    @staticmethod
    def timed(fn) -> tuple[float, object]:
        t0 = time.monotonic()
        out = fn()
        return time.monotonic() - t0, out
