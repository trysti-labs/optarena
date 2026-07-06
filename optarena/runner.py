"""
optarena/runner.py
------------------
Executes one scenario: for each case, prepare a clean workspace subdir, invoke
the driver, and collect CaseResults into a RunRecord (the CLI persists it via
store.save_run).

Optional (both default to the historical behaviour):
- ``trials=N``   - run each case N times; ``passed`` is the majority verdict
  and per-trial detail lands in ``extra`` (agent runs are stochastic; one
  trial overstates certainty). Ignored for drivers that cache results from
  ``prepare()`` (the VS Code UI drivers).
- ``parallel=N`` - fan cases out over N worker threads for drivers marked
  ``parallel_safe`` (baselines, CLI agents). UI drivers stay serial.
"""

from __future__ import annotations

import shutil
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .cases import DOCKER_IMAGE_DEFAULT, DockerSandbox, load_cases
from .drivers import get_driver
from .drivers.base import CaseResult
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


def _merge_trials(case_name: str, results: list[CaseResult]) -> CaseResult:
    """
    Collapse N trial results into one CaseResult.

    ``passed`` is the majority verdict. A single trial is returned unchanged,
    so trials=1 is byte-identical to the historical behaviour.
    """
    if len(results) == 1:
        return results[0]

    passes = sum(1 for r in results if r.passed)
    passed = passes * 2 > len(results)
    errors = [r.error for r in results if r.error]
    last_failed = next((r for r in reversed(results) if not r.passed), None)

    merged = CaseResult(
        name=case_name,
        passed=passed,
        duration_s=statistics.mean(r.duration_s for r in results),
        files=results[-1].files,
        failures=[] if passed else (last_failed.failures if last_failed else []),
        error=errors[-1] if len(errors) == len(results) else None,
    )
    merged.extra = dict(results[-1].extra)
    merged.extra.update({
        "trials": len(results),
        "passes": passes,
        "pass_rate_trials": round(passes / len(results), 3),
        "durations_s": [round(r.duration_s, 2) for r in results],
        "oracle_all_trials": [r.extra.get("oracle") for r in results],
    })
    return merged


def _run_case(driver, case: dict, scenario: Scenario, root: Path, trials: int) -> CaseResult:
    """Run one case (possibly multiple trials), each in a fresh workspace."""
    results: list[CaseResult] = []
    for trial in range(trials):
        ws = root / case["name"] if trials == 1 else root / case["name"] / f"t{trial + 1}"
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
        ws.mkdir(parents=True, exist_ok=True)
        results.append(driver.run_case(case, scenario, ws))
    return _merge_trials(case["name"], results)


def _describe_oracle(oracle: dict | None) -> str | None:
    """One-line summary of what the oracle actually did for one trial."""
    if not oracle or not oracle.get("check_command"):
        return None
    if not oracle.get("ran"):
        return f'check_command not run (file checks failed first): {oracle["check_command"]}'
    sandbox = oracle.get("sandbox")
    container = oracle.get("container")
    where = (f'docker:{oracle.get("image")} (container {container})' if sandbox == "docker" and container else
             f'docker:{oracle.get("image")}' if sandbox == "docker" else
             "host (no Docker sandbox)" if sandbox == "host" else "?")
    failure_class = f' [{oracle["failure_class"]}]' if oracle.get("failure_class") else ""
    return (f'check_command via {where}, exit {oracle.get("exit_code")}{failure_class}, '
            f'{oracle.get("duration_s")}s: {oracle["check_command"]}')


def _print_result(result: CaseResult) -> None:
    status = "PASS" if result.passed else ("ERROR" if result.error else "FAIL")
    detail = "" if result.passed else (
        f" - {result.error or '; '.join(result.failures[:1])}")
    trial_note = (f" [{result.extra.get('passes')}/{result.extra.get('trials')} trials]"
                  if result.extra.get("trials") else "")
    print(f" {status} ({result.duration_s:.1f}s){trial_note}{detail}")

    test_files = result.extra.get("oracle", {}).get("test_setup_files") or []
    if test_files:
        print(f"         test files (hidden from the model): {', '.join(test_files)}")

    diff = result.extra.get("oracle", {}).get("diff")
    if diff and diff.get("files_changed"):
        print(f"         diff: {diff['files_changed']} file(s), "
              f"~{diff['lines_changed_approx']} line(s) changed")

    oracle_trials = result.extra.get("oracle_all_trials")
    if oracle_trials is not None:
        for i, oracle in enumerate(oracle_trials, 1):
            line = _describe_oracle(oracle)
            if line:
                print(f"         [trial {i}] {line}")
    else:
        line = _describe_oracle(result.extra.get("oracle"))
        if line:
            print(f"         {line}")

    # On failure the check_command tail is already embedded in `detail` above;
    # on success there is no failure detail line, so show the captured output
    # here as proof the sandboxed test actually ran (not just exit-code 0).
    output = result.extra.get("oracle", {}).get("output")
    if output and result.passed:
        first_line = output.splitlines()[0] if output.splitlines() else output
        print(f"         output: {first_line}" + (" [...see saved run for full output]" if "\n" in output else ""))


def run_scenario(
    scenario: Scenario,
    workspace_root: Path | None = None,
    trials: int = 1,
    parallel: int = 1,
) -> RunRecord:
    cases = load_cases(scenario.cases, cases_dir=scenario.cases_dir)
    driver = get_driver(scenario.driver)

    trials = max(1, int(trials))
    if trials > 1 and driver.caches_results:
        print(f"  [runner] NOTE: {scenario.driver} runs all cases in one session; "
              f"--trials ignored for this driver")
        trials = 1
    parallel = max(1, int(parallel))
    if parallel > 1 and not driver.parallel_safe:
        print(f"  [runner] NOTE: {scenario.driver} is not parallel-safe; running serially")
        parallel = 1

    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}_{scenario.name}"
    record = RunRecord(
        run_id=run_id,
        scenario=scenario.to_dict(),
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )

    root = workspace_root or Path(tempfile.mkdtemp(prefix="optarena_"))
    root.mkdir(parents=True, exist_ok=True)

    print(f"\n=== RUN {run_id}  driver={scenario.driver}  "
          f"backend={scenario.backend.label()}  cases={len(cases)}"
          + (f"  trials={trials}" if trials > 1 else "")
          + (f"  parallel={parallel}" if parallel > 1 else "") + " ===")

    # One shared container per distinct image needed by this run's cases -
    # not one per check_command call, and not just one overall (a run mixing
    # e.g. a Python case and a Go case needs both toolchains at once). Only
    # started for images some case actually needs via check_command; a no-op
    # (and no print) otherwise or when Docker/the image isn't available
    # (run_check_command then falls back to the host for that case).
    images_needed = {
        c.get("docker_image") or DOCKER_IMAGE_DEFAULT
        for c in cases if c.get("check_command")
    }
    sandboxes = [DockerSandbox(root, image=image) for image in images_needed]

    driver.prepare(scenario, root)
    try:
        for sandbox in sandboxes:
            sandbox.start()
        if parallel > 1:
            with ThreadPoolExecutor(max_workers=parallel) as pool:
                futures = [pool.submit(_run_case, driver, case, scenario, root, trials)
                           for case in cases]
                results = [f.result() for f in futures]
            for result in results:
                print(f"  [case] {result.name} ...", end="")
                _print_result(result)
        else:
            results = []
            for case in cases:
                print(f"  [case] {case['name']} ...", end="", flush=True)
                result = _run_case(driver, case, scenario, root, trials)
                _print_result(result)
                results.append(result)

        record.cases = [r.to_dict() for r in results]
    finally:
        for sandbox in sandboxes:
            sandbox.stop()
        driver.teardown()

    record.summary = aggregate(record.cases)
    return record
