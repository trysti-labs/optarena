"""Per-run/per-case result assembly and console formatting: `RunRecord`,
trial merging, and the human-readable per-case result line."""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from ..drivers.base import CaseResult
from ..events import RunEvents


def safe_run_name(name: str) -> str:
    """
    Filename-safe form of a scenario name. Names default to
    `<driver>-<model>`, and model ids routinely contain "/" (openrouter-style
    ids) or ":" (Ollama tags) - written literally, "/" makes store.save_run
    fail with FileNotFoundError AFTER the whole run has been paid for, and
    ":" breaks Windows. Squash anything path-hostile.
    """
    return re.sub(r"[^\w.\-+]+", "-", name)


@dataclass
class RunRecord:
    run_id: str
    scenario: dict
    started_at: str
    cases: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)
    # F-02: "running" while the case loop is in progress, "completed" once
    # every case finished normally, "interrupted" if the process was
    # killed/Ctrl-C'd mid-run, "error" for a driver/sandbox setup failure
    # before any case ran. None only for records predating this field.
    status: "str | None" = None

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
        # Union across trials (first-seen order): showing only the last
        # trial's files could display a failing trial's file list under a
        # majority-pass verdict.
        files=sorted({f for r in results for f in r.files}),
        failures=[] if passed else (last_failed.failures if last_failed else []),
        error=errors[-1] if len(errors) == len(results) else None,
        # True only if EVERY trial's tool invocation reported clean execution
        # - "did every trial run cleanly", not folded into the passed/failed
        # majority vote (each trial's own `passed` already accounts for its
        # own execution_ok, so the vote is unaffected either way).
        execution_ok=all(r.execution_ok for r in results),
    )
    # H-05: cost/token/turn telemetry is PER TRIAL - keeping only the last
    # trial's `extra` (the historical behaviour) silently discarded every
    # other trial's cost and tokens, understating a `--trials N` run's real
    # spend by up to a factor of N. Sum numeric fields across all trials;
    # `stderr` is free text, so concatenate rather than add; `oracle` is
    # reported per trial separately via `oracle_all_trials` below.
    merged.extra = {}
    for r in results:
        for key, val in r.extra.items():
            if key == "oracle":
                continue
            elif key == "stderr":
                merged.extra["stderr"] = merged.extra.get("stderr", "") + val
            elif key in ("steps", "n_steps"):
                # Per-step trajectory is per-trial; keep the representative
                # (last) trial's rather than summing step counts across trials.
                merged.extra[key] = val
            elif isinstance(val, (int, float)) and not isinstance(val, bool):
                merged.extra[key] = merged.extra.get(key, 0) + val
            else:
                merged.extra[key] = val
    merged.extra.update({
        "trials": len(results),
        "passes": passes,
        "pass_rate_trials": round(passes / len(results), 3),
        "durations_s": [round(r.duration_s, 2) for r in results],
        "oracle_all_trials": [r.extra.get("oracle") for r in results],
    })
    return merged


def _describe_oracle(oracle: dict | None) -> str | None:
    """One-line summary of what the oracle actually did for one trial."""
    if not oracle or not oracle.get("check_command"):
        return None
    if not oracle.get("ran"):
        return f'check_command not run (file checks failed first): {oracle["check_command"]}'
    sandbox = oracle.get("sandbox")
    container = oracle.get("container")
    engine = oracle.get("engine") or "docker"
    where = (f'{engine}:{oracle.get("image")} (container {container})' if sandbox == "docker" and container else
             f'{engine}:{oracle.get("image")}' if sandbox == "docker" else
             "host (no container sandbox)" if sandbox == "host" else "?")
    failure_class = f' [{oracle["failure_class"]}]' if oracle.get("failure_class") else ""
    return (f'check_command via {where}, exit {oracle.get("exit_code")}{failure_class}, '
            f'{oracle.get("duration_s")}s: {oracle["check_command"]}')


def _print_result(result: CaseResult, events: RunEvents) -> None:
    status = "PASS" if result.passed else ("ERROR" if result.error else "FAIL")
    # Phase 2.7: when the tool itself exited non-zero but the workspace
    # otherwise satisfies the oracle (failures is empty), `result.error` and
    # `result.failures` are BOTH empty - without this, the line would print
    # no reason at all for an otherwise-inexplicable FAIL.
    exec_note = "tool reported failure (non-zero exit) despite the artifact " \
                "otherwise passing - see extra.stderr" if not result.execution_ok and not result.failures else None
    detail = "" if result.passed else (
        f" - {result.error or '; '.join(result.failures[:1]) or exec_note}")
    trial_note = (f" [{result.extra.get('passes')}/{result.extra.get('trials')} trials]"
                  if result.extra.get("trials") else "")
    events.say(f" {status} ({result.duration_s:.1f}s){trial_note}{detail}")
    # F-18: case_completed fires here (not a separate call at each call
    # site) so its `status`/`duration_s` always match what the human line
    # just showed - one source of truth for one case's outcome.
    events.emit("case_completed", case=result.name, status=status,
                duration_s=result.duration_s, passed=result.passed)

    # F-18: everything below is a SECONDARY per-case line - .detail(), not
    # .say(), so --log-level warn/error drop it and keep only the PASS/FAIL
    # headline above plus (still via .say()) failure attribution below.
    test_files = result.extra.get("oracle", {}).get("test_setup_files") or []
    if test_files:
        events.detail(f"         test files (hidden from the model): {', '.join(test_files)}")

    diff = result.extra.get("oracle", {}).get("diff")
    if diff and diff.get("files_changed"):
        events.detail(f"         diff: {diff['files_changed']} file(s), "
                      f"~{diff['lines_changed_approx']} line(s) changed")

    # Trajectory: flag off-target edits (files the task never asked for) - a
    # "did it stay on task" signal even when the case passes. Read from the
    # single-run oracle or, for --trials, the last trial's.
    oracle = result.extra.get("oracle") or {}
    traj = oracle.get("trajectory") or next(
        (o.get("trajectory") for o in reversed(result.extra.get("oracle_all_trials") or [])
         if o and o.get("trajectory")), None) or {}
    if traj.get("off_target_count"):
        shown = ", ".join(traj.get("off_target_files", [])[:5])
        more = "" if traj["off_target_count"] <= 5 else f" (+{traj['off_target_count'] - 5} more)"
        note = " despite PASS" if result.passed else ""
        events.detail(f"         off-target edits{note}: {traj['off_target_count']} file(s){' - ' + shown if shown else ''}{more}")
    if result.extra.get("n_steps", 0) and result.extra["n_steps"] > 1:
        events.detail(f"         steps: {result.extra['n_steps']} (per-step timing/tokens in saved run)")

    # Tier 1 (dynamic eval): note any mid-session disruptions that fired.
    fired = [d for st in (result.extra.get("steps") or []) for d in (st.get("disrupted") or [])]
    if fired:
        events.detail(f"         disruptions fired: {'; '.join(fired)}")

    # Tier 3 (failure attribution): for a failed multi-step case, say where -
    # kept at .say() (not .detail()), unlike the rest of this function: this
    # is the one line beyond the PASS/FAIL headline that --log-level warn/error
    # is meant to keep.
    if not result.passed:
        from ..metrics import attribute_failure
        why = attribute_failure(result.to_dict())
        if why:
            events.say(f"         attribution: {why}")

    # Security scan (opt-in --security-scan): flag secrets/injection/unsafe calls
    # the agent introduced, even when the case passes its correctness oracle.
    sec = result.extra.get("security")
    if sec and sec.get("total"):
        c = sec.get("counts", {})
        events.detail(f"         security: {sec['total']} finding(s) "
                      f"({c.get('error', 0)} error, {c.get('warning', 0)} warning, {c.get('note', 0)} note)")
        for f in sec["findings"][:3]:
            events.detail(f"           - [{f['level']}] {f['title']} ({f['file']}:{f['line']})")

    oracle_trials = result.extra.get("oracle_all_trials")
    if oracle_trials is not None:
        for i, oracle in enumerate(oracle_trials, 1):
            line = _describe_oracle(oracle)
            if line:
                events.detail(f"         [trial {i}] {line}")
    else:
        line = _describe_oracle(result.extra.get("oracle"))
        if line:
            events.detail(f"         {line}")

    # On failure the check_command tail is already embedded in `detail` above;
    # on success there is no failure detail line, so show the captured output
    # here as proof the sandboxed test actually ran (not just exit-code 0).
    output = result.extra.get("oracle", {}).get("output")
    if output and result.passed:
        first_line = output.splitlines()[0] if output.splitlines() else output
        events.detail(f"         output: {first_line}" + (" [...see saved run for full output]" if "\n" in output else ""))
