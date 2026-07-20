"""
optarena/metrics.py
────────────────
Aggregate metrics over a run's CaseResult dicts, and per-case deltas between
two runs. Kept dependency-free (stdlib only).
"""

from __future__ import annotations

import statistics


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
    return {
        "cases": total,
        "passed": passed,
        "failed": total - passed,
        "errors": errors,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "total_duration_s": round(sum(durations), 1),
        "mean_duration_s": round(statistics.mean(durations), 1) if durations else 0.0,
        "median_duration_s": round(statistics.median(durations), 1) if durations else 0.0,
        "p95_duration_s": _percentile(durations, 95),
        "total_tokens": tokens or None,
        "total_cost_usd": round(cost, 4) if cost else None,
        "mean_files_changed": round(statistics.mean(files_changed), 1) if files_changed else 0.0,
        "flaky_cases": flaky,
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
