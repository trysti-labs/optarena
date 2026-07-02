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
    durations = [c["duration_s"] for c in case_dicts if c.get("duration_s")]
    tokens = sum(c.get("extra", {}).get("total_tokens")
                 or (c.get("extra", {}).get("prompt_tokens", 0)
                     + c.get("extra", {}).get("completion_tokens", 0))
                 for c in case_dicts)
    return {
        "cases": total,
        "passed": passed,
        "failed": total - passed,
        "errors": errors,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "total_duration_s": round(sum(durations), 1),
        "mean_duration_s": round(statistics.mean(durations), 1) if durations else 0.0,
        "median_duration_s": round(statistics.median(durations), 1) if durations else 0.0,
        "total_tokens": tokens or None,
    }


def case_deltas(run_a: dict, run_b: dict) -> list[dict]:
    """Per-case side-by-side rows for two runs (aligned by case name)."""
    a_cases = {c["name"]: c for c in run_a["cases"]}
    b_cases = {c["name"]: c for c in run_b["cases"]}
    rows = []
    for name in sorted(set(a_cases) | set(b_cases)):
        ca, cb = a_cases.get(name), b_cases.get(name)
        rows.append({
            "case": name,
            "a_passed": ca and ca.get("passed"),
            "b_passed": cb and cb.get("passed"),
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
