"""
optarena/metrics.py
────────────────
Aggregate metrics over a run's CaseResult dicts, and per-case deltas between
two runs. Kept dependency-free (stdlib only).
"""

from __future__ import annotations

import math
import statistics


def wilson_ci(passed: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """
    Wilson score confidence interval for a pass rate (default 95%, z=1.96).

    A point pass rate ("50%") on a handful of cases is nearly meaningless
    without a spread - 3/6 and 300/600 are both "50%" but only one is a claim.
    Wilson (not the normal approximation) stays inside [0, 1] and is well-behaved
    for small n and for rates near 0 or 1, which is exactly the regime a
    per-tool eval runs in. Returns (low, high), each rounded to 3 dp.
    """
    if total <= 0:
        return (0.0, 0.0)
    phat = passed / total
    denom = 1 + z * z / total
    center = (phat + z * z / (2 * total)) / denom
    margin = (z / denom) * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total))
    return (round(max(0.0, center - margin), 3), round(min(1.0, center + margin), 3))


def case_trajectory(c: dict) -> dict:
    """Extract a case's trajectory dict, whether it's a single run
    (``extra.oracle.trajectory``) or a ``--trials`` merge (last trial's
    ``extra.oracle_all_trials[*].trajectory``). Empty dict when absent."""
    ex = c.get("extra", {}) or {}
    oracle = ex.get("oracle") or {}
    if oracle.get("trajectory"):
        return oracle["trajectory"]
    for o in reversed(ex.get("oracle_all_trials") or []):
        if o and o.get("trajectory"):
            return o["trajectory"]
    return {}


def is_clean_pass(c: dict) -> bool:
    """Passed AND the tool exited cleanly AND it touched no off-target files -
    the 'right answer via a clean path' the trajectory-eval literature asks for,
    as opposed to a pass that also thrashed unrelated files or exited non-zero."""
    if not c.get("passed"):
        return False
    if c.get("execution_ok") is False:
        return False
    return case_trajectory(c).get("off_target_count", 0) == 0


def attribute_failure(c: dict) -> str | None:
    """
    For a FAILED case with per-step records, a one-line attribution of *where* it
    went wrong across the prompts - the coding analog of multi-agent failure
    attribution (Tier 3). Uses the per-step ``ok``/``disrupted`` flags every
    driver records, plus whichever verdict signal is present per step:

    - ``oracle_ok`` (precise): the REAL oracle (expected files AND
      check_command) evaluated at that step - only present for cases with
      ``disruptions``, since running check_command after every prompt of
      every case would multiply container execs for no benefit elsewhere.
    - ``expected_ok`` (cheap fallback): just the expected-file shape check,
      recorded for every case/step regardless.

    Returns None when there's nothing to attribute (passed, or fewer than two
    steps).
    """
    if c.get("passed"):
        return None
    steps = (c.get("extra", {}) or {}).get("steps") or []
    if len(steps) < 2:
        return None
    precise = any("oracle_ok" in st for st in steps)
    verdict_key = "oracle_ok" if precise else "expected_ok"
    verb = "the case's real oracle (behavior) passed" if precise else "expected files satisfied"
    last_ok = max((st["i"] for st in steps if st.get(verdict_key)), default=None)
    last_i = steps[-1]["i"]
    if last_ok is not None and last_ok < last_i:
        after = next((st for st in steps if st["i"] == last_ok), {})
        disrupted = after.get("disrupted")
        why = f" (disruption fired here: {disrupted[0]})" if disrupted else ""
        return f"{verb} after prompt {last_ok}, regressed by prompt {last_i}{why}"
    first_bad = next((st["i"] for st in steps if st.get("ok") is False), None)
    if first_bad is not None:
        return f"tool reported failure at prompt {first_bad}"
    if last_ok is None:
        return ("the case's real oracle never passed at any prompt" if precise
                else "expected files were never satisfied by any prompt")
    return None


def aggregate(case_dicts: list[dict]) -> dict:
    """Summary metrics for one run."""
    total = len(case_dicts)
    passed = sum(1 for c in case_dicts if c.get("passed"))
    errors = sum(1 for c in case_dicts if c.get("error"))
    # `is not None`, not truthiness: an explicit 0.0s duration (an instant
    # failure, a cached result) is a real sample and excluding it silently
    # inflates the mean/median/percentiles (audit L-01).
    durations = [c["duration_s"] for c in case_dicts if c.get("duration_s") is not None]
    tokens = sum(c.get("extra", {}).get("total_tokens")
                 or (c.get("extra", {}).get("prompt_tokens", 0)
                     + c.get("extra", {}).get("completion_tokens", 0))
                 for c in case_dicts)
    cost = sum(c.get("extra", {}).get("cost_usd", 0) or 0 for c in case_dicts)
    files_changed = [len(c.get("files", []) or []) for c in case_dicts]
    # Flaky = a --trials case that neither always passed nor always failed.
    # The majority verdict hides this; a "2/3" is a weaker claim than "3/3".
    flaky = sum(
        1 for c in case_dicts
        if c.get("extra", {}).get("trials")
        and 0 < c["extra"].get("passes", 0) < c["extra"]["trials"]
    )
    # Trajectory rollups ("judge the path"): how many passes were CLEAN (no
    # off-target edits, clean exit), and how many files were touched beyond
    # what the tasks asked for across the whole run.
    clean_passes = sum(1 for c in case_dicts if is_clean_pass(c))
    off_target = sum(case_trajectory(c).get("off_target_count", 0) for c in case_dicts)
    # Security-scan rollup (only when --security-scan ran; None otherwise so a
    # non-scanned run doesn't read as "0 findings = clean").
    scanned = [c for c in case_dicts if isinstance(c.get("extra", {}).get("security"), dict)]
    if scanned:
        sec_total = sum(c["extra"]["security"].get("total", 0) for c in scanned)
        sec_high = sum(c["extra"]["security"].get("counts", {}).get("error", 0) for c in scanned)
    else:
        sec_total = sec_high = None
    step_counts = [c["extra"]["n_steps"] for c in case_dicts
                   if isinstance(c.get("extra", {}).get("n_steps"), int)]
    ci_low, ci_high = wilson_ci(passed, total)
    # Efficiency / long-horizon rollups (Tier 2, LoCoBench-Agent's cost framing):
    # how much did each *success* cost? tokens-per-pass and steps-per-pass make
    # a "cheaper to get right" comparison first-class, not just pass rate.
    tokens_per_pass = round(tokens / passed) if (tokens and passed) else None
    pass_steps = [c["extra"]["n_steps"] for c in case_dicts
                  if c.get("passed") and isinstance(c.get("extra", {}).get("n_steps"), int)]
    steps_per_pass = round(statistics.mean(pass_steps), 1) if pass_steps else None
    return {
        "cases": total,
        "passed": passed,
        "failed": total - passed,
        "errors": errors,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        # 95% Wilson interval on the pass rate - the spread that says whether a
        # pass-rate difference is a real signal or small-sample noise.
        "pass_rate_ci": [ci_low, ci_high],
        "total_duration_s": round(sum(durations), 1),
        "mean_duration_s": round(statistics.mean(durations), 1) if durations else 0.0,
        "median_duration_s": round(statistics.median(durations), 1) if durations else 0.0,
        "p95_duration_s": _percentile(durations, 95),
        "total_tokens": tokens or None,
        "total_cost_usd": round(cost, 4) if cost else None,
        "mean_files_changed": round(statistics.mean(files_changed), 1) if files_changed else 0.0,
        "flaky_cases": flaky,
        # Trajectory: clean passes and total off-target (unrequested) edits.
        "clean_passes": clean_passes,
        "off_target_edits": off_target,
        "mean_steps": round(statistics.mean(step_counts), 1) if step_counts else None,
        # Security scan (None unless --security-scan ran).
        "security_findings": sec_total,
        "security_high": sec_high,
        # Efficiency: cost of each success (None when no token/step telemetry).
        "tokens_per_pass": tokens_per_pass,
        "steps_per_pass": steps_per_pass,
    }


def _percentile(values: list[float], pct: int) -> float:
    """Nearest-rank percentile, safe for tiny samples (n<2 -> the value itself)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    k = max(0, min(len(ordered) - 1, round(pct / 100 * len(ordered)) - 1))
    return round(ordered[k], 1)


def case_deltas(run_a: dict, run_b: dict) -> list[dict]:
    """Per-case side-by-side rows for two runs (aligned by case name)."""
    a_cases = {c["name"]: c for c in run_a["cases"]}
    b_cases = {c["name"]: c for c in run_b["cases"]}
    rows = []
    def _trials(c):
        """'2/3' stability marker for a --trials case, else None."""
        extra = (c or {}).get("extra", {})
        if extra.get("trials"):
            return f'{extra.get("passes", 0)}/{extra["trials"]}'
        return None

    for name in sorted(set(a_cases) | set(b_cases)):
        ca, cb = a_cases.get(name), b_cases.get(name)
        rows.append({
            "case": name,
            "a_passed": ca and ca.get("passed"),
            "b_passed": cb and cb.get("passed"),
            "a_trials": _trials(ca),
            "b_trials": _trials(cb),
            "a_duration_s": ca and ca.get("duration_s"),
            "b_duration_s": cb and cb.get("duration_s"),
            "duration_delta_s": (
                round(cb["duration_s"] - ca["duration_s"], 2)
                if ca and cb and ca.get("duration_s") is not None
                and cb.get("duration_s") is not None else None
            ),
            "a_failures": (ca or {}).get("failures", []),
            "b_failures": (cb or {}).get("failures", []),
        })
    return rows
