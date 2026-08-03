"""
optarena/runner/
─────────────────
Executes one scenario: for each case, prepare a clean workspace subdir, invoke
the driver, and collect CaseResults into a RunRecord (the CLI persists it via
store.save_run).

Optional (both default to the historical behaviour):
- ``trials=N``   - run each case N times; ``passed`` is the majority verdict
  and per-trial detail lands in ``extra`` (agent runs are stochastic; one
  trial overstates certainty). Ignored for drivers that cache results from
  ``prepare()`` (see ``Driver.caches_results`` - no current driver sets it).
- ``parallel=N`` - fan cases out over N worker threads for drivers marked
  ``parallel_safe`` (baselines, CLI agents); other drivers stay serial. Each
  worker gets its own dedicated sandbox container per image (F-06), not one
  container shared across workers.

P3-01: split by concern out of what used to be one 783-line `runner.py` -
`_manifest.py` (manifest-building), `_results.py` (RunRecord/trial-merging/
console formatting), `_execution.py` (case execution, the --parallel worker
pool, and `run_scenario` itself, which ties the other two together). This
file re-exports everything another module or the test suite imports from
`optarena.runner` directly, so `from .runner import X` needed no changes
elsewhere in the codebase.
"""

from __future__ import annotations

from ._execution import _COLLECT_POLL_S, _run_case, _run_parallel, _worker_loop, run_scenario
from ._manifest import ORACLE_VERSION, build_manifest
from ._results import RunRecord, _describe_oracle, _merge_trials, _print_result, safe_run_name

__all__ = [
    "ORACLE_VERSION", "build_manifest",
    "safe_run_name", "RunRecord", "_merge_trials", "_describe_oracle", "_print_result",
    "_run_case", "_worker_loop", "_run_parallel", "_COLLECT_POLL_S", "run_scenario",
]
