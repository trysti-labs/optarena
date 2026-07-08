"""
optarena/compare.py
────────────────
Side-by-side comparison of two runs: per-case pass/duration deltas plus a
verdict block. Saved next to the runs so the dashboard can render it, and
printed as a terminal table.
"""

from __future__ import annotations

import json
import re
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
            "cheaper": _cheaper(a_label, b_label, sa, sb),
        },
    }


def _cheaper(a_label: str, b_label: str, sa: dict, sb: dict) -> str | None:
    """Which run cost less USD, or None when neither run had a priced cost."""
    ca, cb = sa.get("total_cost_usd"), sb.get("total_cost_usd")
    if not ca and not cb:
        return None
    ca, cb = ca or 0.0, cb or 0.0
    return a_label if ca <= cb else b_label


def save_comparison(cmp: dict) -> Path:
    out_dir = RESULTS_DIR / "comparisons"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Labels are scenario names, which can embed model ids with "/" or ":" -
    # sanitize so the comparison file lands where intended on every platform.
    safe = lambda s: re.sub(r"[^\w.\-+]+", "-", s)  # noqa: E731
    name = f"{time.strftime('%Y%m%d-%H%M%S')}_{safe(cmp['a']['label'])}_vs_{safe(cmp['b']['label'])}.json"
    path = out_dir / name
    from .store import _write_atomic
    _write_atomic(path, json.dumps(cmp, indent=2))
    return path


def regression_summary(cmp: dict) -> dict:
    """
    Regression-focused view of an existing comparison: which named cases
    flipped from passing to failing (or vice versa) between A and B, plus
    accuracy/time/token deltas - "did the upgrade help or hurt?" in one
    glance, not a full per-case table.
    """
    regressed = [r["case"] for r in cmp["cases"] if r["a_passed"] and not r["b_passed"]]
    improved = [r["case"] for r in cmp["cases"] if not r["a_passed"] and r["b_passed"]]
    sa, sb = cmp["a"]["summary"], cmp["b"]["summary"]
    ta, tb = sa.get("total_tokens"), sb.get("total_tokens")
    token_delta = (tb - ta) if (ta is not None and tb is not None) else None
    token_delta_pct = round(token_delta / ta * 100, 1) if token_delta is not None and ta else None
    ca, cb = sa.get("total_cost_usd"), sb.get("total_cost_usd")
    cost_delta = round(cb - ca, 4) if (ca is not None and cb is not None) else None
    cost_delta_pct = round(cost_delta / ca * 100, 1) if cost_delta is not None and ca else None
    return {
        "a_label": cmp["a"]["label"], "b_label": cmp["b"]["label"],
        "pass_rate_a": sa["pass_rate"], "pass_rate_b": sb["pass_rate"],
        "pass_rate_delta_pp": round((sb["pass_rate"] - sa["pass_rate"]) * 100, 1),
        "mean_duration_a": sa["mean_duration_s"], "mean_duration_b": sb["mean_duration_s"],
        "mean_duration_delta_s": round(sb["mean_duration_s"] - sa["mean_duration_s"], 2),
        "tokens_a": ta, "tokens_b": tb,
        "token_delta": token_delta, "token_delta_pct": token_delta_pct,
        "cost_a": ca, "cost_b": cb, "cost_delta": cost_delta, "cost_delta_pct": cost_delta_pct,
        "regressed_cases": regressed,
        "improved_cases": improved,
    }


def format_regression(summary: dict) -> str:
    """Terminal-friendly regression report: 'did the upgrade help or hurt?'."""
    s = summary
    lines = [
        f"\n  {s['a_label']}  ->  {s['b_label']}",
        "",
        f"  accuracy    {s['pass_rate_a']:.0%} -> {s['pass_rate_b']:.0%}  "
        f"({'+' if s['pass_rate_delta_pp'] >= 0 else ''}{s['pass_rate_delta_pp']}pp)",
        f"  mean time   {s['mean_duration_a']:.1f}s -> {s['mean_duration_b']:.1f}s  "
        f"({'+' if s['mean_duration_delta_s'] >= 0 else ''}{s['mean_duration_delta_s']}s)",
    ]
    if s.get("cost_a") is not None and s.get("cost_b") is not None:
        pct = f", {'+' if s['cost_delta_pct'] >= 0 else ''}{s['cost_delta_pct']}%" if s["cost_delta_pct"] is not None else ""
        lines.append(
            f"  cost        ${s['cost_a']:.2f} -> ${s['cost_b']:.2f}  "
            f"({'+' if s['cost_delta'] >= 0 else ''}${s['cost_delta']:.2f}{pct})"
        )
    if s["tokens_a"] is not None and s["tokens_b"] is not None:
        pct = f", {'+' if s['token_delta_pct'] >= 0 else ''}{s['token_delta_pct']}%" if s["token_delta_pct"] is not None else ""
        lines.append(
            f"  tokens      {s['tokens_a']} -> {s['tokens_b']}  "
            f"({'+' if s['token_delta'] >= 0 else ''}{s['token_delta']}{pct})"
        )
    lines.append("")
    lines.append(f"  regressed cases (passed in A, failed in B): {len(s['regressed_cases'])}")
    for name in s["regressed_cases"]:
        lines.append(f"    - {name}")
    if not s["regressed_cases"]:
        lines.append("    (none)")
    lines.append("")
    lines.append(f"  improved cases (failed in A, passed in B): {len(s['improved_cases'])}")
    for name in s["improved_cases"]:
        lines.append(f"    - {name}")
    if not s["improved_cases"]:
        lines.append("    (none)")
    lines.append("")
    return "\n".join(lines)


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
        mark = lambda v: "-" if v is None else ("PASS" if v else "fail")  # noqa: E731
        t = lambda v: "-" if v is None else f"{v:.1f}s"                    # noqa: E731
        lines.append(
            f"  {row['case']:30} {mark(row['a_passed']):>10} {mark(row['b_passed']):>10} "
            f"{t(row['a_duration_s']):>8} {t(row['b_duration_s']):>8}"
        )
    sa, sb = a["summary"], b["summary"]
    v = cmp["verdict"]
    lines += [
        f"  {'-'*70}",
        f"  pass rate:     {sa['pass_rate']:.0%}  vs  {sb['pass_rate']:.0%}"
        f"   -> more accurate: {v['more_accurate']}",
        f"  mean duration: {sa['mean_duration_s']:.1f}s vs {sb['mean_duration_s']:.1f}s"
        f"   -> faster: {v['faster']}",
    ]
    if v.get("cheaper"):
        ca = sa.get("total_cost_usd") or 0.0
        cb = sb.get("total_cost_usd") or 0.0
        lines.append(
            f"  cost:          ${ca:.2f}  vs  ${cb:.2f}   -> cheaper: {v['cheaper']}"
        )
    lines.append("")
    return "\n".join(lines)
