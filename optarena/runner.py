"""
optarena/runner.py
───────────────
Executes one scenario: for each case, prepare a clean workspace subdir, invoke
the driver, collect CaseResults into a RunRecord, and persist it via store.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .cases import load_cases
from .drivers import get_driver
from .metrics import aggregate
from .scenario import Scenario


@dataclass
class RunRecord:
    run_id: str
    scenario: dict
    started_at: str
    cases: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return vars(self)


def run_scenario(scenario: Scenario, workspace_root: Path | None = None) -> RunRecord:
    cases = load_cases(scenario.cases)
    driver = get_driver(scenario.driver)

    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}_{scenario.name}"
    record = RunRecord(
        run_id=run_id,
        scenario=scenario.to_dict(),
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )

    root = workspace_root or Path(tempfile.mkdtemp(prefix="optarena_"))
    root.mkdir(parents=True, exist_ok=True)

    print(f"\n=== RUN {run_id}  driver={scenario.driver}  "
          f"backend={scenario.backend.label()}  cases={len(cases)} ===")
    driver.prepare(scenario, root)
    try:
        for case in cases:
            ws = root / case["name"]
            if ws.exists():
                shutil.rmtree(ws, ignore_errors=True)
            ws.mkdir(parents=True, exist_ok=True)

            print(f"  [case] {case['name']} ...", end="", flush=True)
            result = driver.run_case(case, scenario, ws)
            status = "PASS" if result.passed else ("ERROR" if result.error else "FAIL")
            print(f" {status} ({result.duration_s:.1f}s)"
                  + (f" — {result.error or '; '.join(result.failures[:1])}"
                     if not result.passed else ""))
            record.cases.append(result.to_dict())
    finally:
        driver.teardown()

    record.summary = aggregate(record.cases)
    return record
