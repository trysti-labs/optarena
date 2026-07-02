"""
optarena/compare.py
────────────────
Side-by-side comparison of two runs: per-case pass/duration deltas plus a
verdict block. Saved next to the runs so the dashboard can render it, and
printed as a terminal table.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .metrics import case_deltas
from .store import RESULTS_DIR


def compare_runs(run_a: dict, run_b: dict) -> dict:
    a_label = run_a["scenario"]["name"]
    b_label = run_b["scenario"]["name"]
    rows = case_deltas(run_a, run_b)
    sa, sb = run_a["summary"], run_b["summary"]
    return {
        "compared_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "a": {"run_id": run_a["run_id"], "label": a_label, "summary": sa,
              "scenario": run_a["scenario"]},
        "b": {"run_id": run_b["run_id"], "label": b_label, "summary": sb,
              "scenario": run_b["scenario"]},
        "cases": rows,
        "verdict": {
            "pass_rate_delta": round(sb["pass_rate"] - sa["pass_rate"], 3),
            "mean_duration_delta_s": round(
                sb["mean_duration_s"] - sa["mean_duration_s"], 1),
            "faster": a_label if sa["mean_duration_s"] <= sb["mean_duration_s"] else b_label,
            "more_accurate": (
                a_label if sa["pass_rate"] > sb["pass_rate"]
                else b_label if sb["pass_rate"] > sa["pass_rate"] else "tie"
            ),
        },
    }


def save_comparison(cmp: dict) -> Path:
    out_dir = RESULTS_DIR / "comparisons"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}_{cmp['a']['label']}_vs_{cmp['b']['label']}.json"
    path = out_dir / name
    path.write_text(json.dumps(cmp, indent=2), encoding="utf-8")
    return path


def format_table(cmp: dict) -> str:
    """Human-readable comparison for the terminal."""
    a, b = cmp["a"], cmp["b"]
    lines = [
        f"\n{'='*78}",
        f"  {a['label']}  vs  {b['label']}",
        f"{'='*78}",
        f"  {'case':30} {'A':>10} {'B':>10} {'A time':>8} {'B time':>8}",
        f"  {'-'*30} {'-'*10} {'-'*10} {'-'*8} {'-'*8}",
    ]
    for row in cmp["cases"]:
        mark = lambda v: "—" if v is None else ("PASS" if v else "fail")  # noqa: E731
        t = lambda v: "—" if v is None else f"{v:.1f}s"                    # noqa: E731
        lines.append(
            f"  {row['case']:30} {mark(row['a_passed']):>10} {mark(row['b_passed']):>10} "
            f"{t(row['a_duration_s']):>8} {t(row['b_duration_s']):>8}"
        )
    sa, sb = a["summary"], b["summary"]
    v = cmp["verdict"]
    lines += [
        f"  {'-'*70}",
        f"  pass rate:     {sa['pass_rate']:.0%}  vs  {sb['pass_rate']:.0%}"
        f"   → more accurate: {v['more_accurate']}",
        f"  mean duration: {sa['mean_duration_s']:.1f}s vs {sb['mean_duration_s']:.1f}s"
        f"   → faster: {v['faster']}",
        "",
    ]
    return "\n".join(lines)
