"""
optarena/compare.py
────────────────
Side-by-side comparison of two runs: per-case pass/duration deltas plus a
verdict block. Saved next to the runs so the dashboard can render it, and
printed as a terminal table.
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from . import store
from .metrics import case_deltas


def mcnemar_exact_p(regressed: int, improved: int) -> float:
    """
    Two-sided exact McNemar p-value for a paired A/B accuracy delta.

    A/B runs share the case set, so pass/fail is PAIRED per case: the only cases
    that carry signal about "did accuracy really change" are the *discordant*
    ones - passed in A but failed in B (``regressed``), or vice versa
    (``improved``). Under the null "the change flips cases at random", the
    discordant splits follow Binomial(n, 0.5); the exact two-sided p-value is
    ``2 * P(X <= min(regressed, improved))`` capped at 1. This is the correct
    test for paired binary outcomes on small n (no normal approximation), so a
    "+2pp, more accurate" verdict on a handful of cases can be labelled as noise
    rather than a result. Returns 1.0 when there are no discordant pairs.
    """
    n = regressed + improved
    if n == 0:
        return 1.0
    k = min(regressed, improved)
    tail = sum(math.comb(n, i) for i in range(k + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


def manifest_compatibility(man_a: dict | None, man_b: dict | None) -> dict:
    """
    Whether two runs measured the SAME thing, so an overall faster/more-
    accurate/cheaper verdict is meaningful (M-02). Comparability requires the
    same oracle version, the same resolved case set, and the same trial count
    - NOT the same driver or backend (tool-vs-tool and backend-vs-backend are
    exactly what a comparison is for). Returns
    ``{"comparable": bool, "reasons": [...], "verified": bool}``; ``verified``
    is False when either run predates manifests (nothing to check against, so
    we warn rather than block).
    """
    if not man_a or not man_b:
        return {"comparable": True, "verified": False,
                "reasons": ["one or both runs predate run manifests - "
                            "comparability could not be verified"]}
    reasons: list[str] = []
    if man_a.get("oracle_version") != man_b.get("oracle_version"):
        reasons.append(f"different oracle version "
                       f"({man_a.get('oracle_version')} vs {man_b.get('oracle_version')})")
    if man_a.get("case_set_hash") != man_b.get("case_set_hash"):
        reasons.append(f"different case set "
                       f"({man_a.get('case_count')} case(s) hash {man_a.get('case_set_hash')} "
                       f"vs {man_b.get('case_count')} case(s) hash {man_b.get('case_set_hash')})")
    if man_a.get("trials") != man_b.get("trials"):
        reasons.append(f"different trial count "
                       f"({man_a.get('trials')} vs {man_b.get('trials')})")
    return {"comparable": not reasons, "verified": True, "reasons": reasons}


def compare_runs(run_a: dict, run_b: dict, force: bool = False) -> dict:
    a_label = run_a["scenario"]["name"]
    b_label = run_b["scenario"]["name"]
    rows = case_deltas(run_a, run_b)
    sa, sb = run_a["summary"], run_b["summary"]
    compat = manifest_compatibility(run_a.get("manifest"), run_b.get("manifest"))
    # M-02: when two runs measured different things, an aggregate "winner" is
    # misleading (you'd be crowning a tool for scoring higher on an easier or
    # smaller case set). Suppress the aggregate verdict unless the caller
    # explicitly forces it; the per-case delta table (aligned by case name) is
    # still shown, since that IS valid on whatever cases the two share.
    suppress = compat["comparable"] is False and not force
    # Paired significance on the accuracy delta (M-XX): count discordant cases
    # (passed in exactly one of A/B) and run the exact McNemar test, so the
    # accuracy verdict can carry a p-value instead of pretending a small-sample
    # flip is a result. Only paired cases (present with a verdict in both runs)
    # contribute.
    regressed_n = sum(1 for r in rows if r["a_passed"] and r["b_passed"] is False)
    improved_n = sum(1 for r in rows if r["a_passed"] is False and r["b_passed"])
    p_value = mcnemar_exact_p(regressed_n, improved_n)
    verdict = {
        "pass_rate_delta": round(sb["pass_rate"] - sa["pass_rate"], 3),
        "mean_duration_delta_s": round(
            sb["mean_duration_s"] - sa["mean_duration_s"], 1),
        "faster": None if suppress else (
            a_label if sa["mean_duration_s"] <= sb["mean_duration_s"] else b_label),
        "more_accurate": None if suppress else (
            a_label if sa["pass_rate"] > sb["pass_rate"]
            else b_label if sb["pass_rate"] > sa["pass_rate"] else "tie"
        ),
        "cheaper": None if suppress else _cheaper(a_label, b_label, sa, sb),
        "suppressed": suppress,
        # Statistical significance of the accuracy delta (paired, exact McNemar).
        "accuracy_p_value": round(p_value, 4),
        "accuracy_significant": p_value < 0.05,
        "n_discordant": regressed_n + improved_n,
    }
    return {
        "compared_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "a": {"run_id": run_a["run_id"], "label": a_label, "summary": sa,
              "scenario": run_a["scenario"], "manifest": run_a.get("manifest")},
        "b": {"run_id": run_b["run_id"], "label": b_label, "summary": sb,
              "scenario": run_b["scenario"], "manifest": run_b.get("manifest")},
        "cases": rows,
        "compatibility": compat,
        "verdict": verdict,
    }


def _cheaper(a_label: str, b_label: str, sa: dict, sb: dict) -> str | None:
    """Which run cost less USD, or None when either run has no cost data at all.

    H-07: `total_cost_usd` is None when a run has NO cost data whatsoever
    (e.g. a driver that never reports cost) - that is not the same claim as
    "this run cost $0.00" (a real, priced, genuinely-free/local run). The
    previous version only refused a verdict when BOTH sides were falsy,
    so `ca=None` (no data) got coerced to `0.0` and could beat a real
    `cb=$0.50`, declaring the run with no cost measurement "cheaper".
    """
    ca, cb = sa.get("total_cost_usd"), sb.get("total_cost_usd")
    if ca is None or cb is None:
        return None
    return a_label if ca <= cb else b_label


def save_comparison(cmp: dict) -> Path:
    # store.RESULTS_DIR (not a `from .store import RESULTS_DIR` binding at
    # module load) so this always reflects the current location even after
    # store.set_results_dir() (--results-dir) - a plain name import would
    # keep pointing at whatever RESULTS_DIR was at import time.
    out_dir = store.RESULTS_DIR / "comparisons"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Labels are scenario names, which can embed model ids with "/" or ":" -
    # sanitize so the comparison file lands where intended on every platform.
    safe = lambda s: re.sub(r"[^\w.\-+]+", "-", s)  # noqa: E731
    name = f"{time.strftime('%Y%m%d-%H%M%S')}_{safe(cmp['a']['label'])}_vs_{safe(cmp['b']['label'])}.json"
    path = out_dir / name
    store._write_atomic(path, json.dumps(cmp, indent=2))
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

    def _is_flaky(marker):     # "2/3" -> True, "3/3"/"0/3"/None -> False
        if not marker:
            return False
        passes, trials = marker.split("/")
        return 0 < int(passes) < int(trials)

    flaky = [r["case"] for r in cmp["cases"]
             if _is_flaky(r.get("a_trials")) or _is_flaky(r.get("b_trials"))]
    p_value = mcnemar_exact_p(len(regressed), len(improved))
    sa, sb = cmp["a"]["summary"], cmp["b"]["summary"]
    ta, tb = sa.get("total_tokens"), sb.get("total_tokens")
    token_delta = (tb - ta) if (ta is not None and tb is not None) else None
    token_delta_pct = round(token_delta / ta * 100, 1) if token_delta is not None and ta else None
    ca, cb = sa.get("total_cost_usd"), sb.get("total_cost_usd")
    cost_delta = round(cb - ca, 4) if (ca is not None and cb is not None) else None
    cost_delta_pct = round(cost_delta / ca * 100, 1) if cost_delta is not None and ca else None
    return {
        # M-03: carry the manifest-compatibility verdict through, so
        # format_regression can warn as loudly as format_table does - a
        # regression gate comparing two runs that measured different things
        # must say so, not print clean-looking deltas.
        "compatibility": cmp.get("compatibility", {}),
        "a_label": cmp["a"]["label"], "b_label": cmp["b"]["label"],
        "pass_rate_a": sa["pass_rate"], "pass_rate_b": sb["pass_rate"],
        "pass_rate_ci_a": sa.get("pass_rate_ci"), "pass_rate_ci_b": sb.get("pass_rate_ci"),
        "pass_rate_delta_pp": round((sb["pass_rate"] - sa["pass_rate"]) * 100, 1),
        "accuracy_p_value": round(p_value, 4),
        "accuracy_significant": p_value < 0.05,
        "n_discordant": len(regressed) + len(improved),
        "mean_duration_a": sa["mean_duration_s"], "mean_duration_b": sb["mean_duration_s"],
        "mean_duration_delta_s": round(sb["mean_duration_s"] - sa["mean_duration_s"], 2),
        "tokens_a": ta, "tokens_b": tb,
        "token_delta": token_delta, "token_delta_pct": token_delta_pct,
        "cost_a": ca, "cost_b": cb, "cost_delta": cost_delta, "cost_delta_pct": cost_delta_pct,
        "regressed_cases": regressed,
        "improved_cases": improved,
        "flaky_cases": flaky,
    }


def format_regression(summary: dict) -> str:
    """Terminal-friendly regression report: 'did the upgrade help or hurt?'."""
    s = summary
    lines = [
        f"\n  {s['a_label']}  ->  {s['b_label']}",
    ]
    # M-03: same warning format_table shows - deltas between runs that did
    # not measure the same thing are not a like-for-like regression verdict.
    compat = s.get("compatibility") or {}
    if compat.get("reasons"):
        header = ("NOT DIRECTLY COMPARABLE" if compat.get("comparable") is False
                  else "COMPARABILITY UNVERIFIED")
        lines.append(f"  ** {header} **")
        for reason in compat["reasons"]:
            lines.append(f"     - {reason}")
    cia, cib = s.get("pass_rate_ci_a"), s.get("pass_rate_ci_b")
    acc_a = f"{s['pass_rate_a']:.0%}" + (f" [{cia[0]:.0%}-{cia[1]:.0%}]" if cia else "")
    acc_b = f"{s['pass_rate_b']:.0%}" + (f" [{cib[0]:.0%}-{cib[1]:.0%}]" if cib else "")
    lines += [
        "",
        f"  accuracy    {acc_a} -> {acc_b}  "
        f"({'+' if s['pass_rate_delta_pp'] >= 0 else ''}{s['pass_rate_delta_pp']}pp)",
    ]
    # Is the accuracy change real or small-sample noise? (paired exact McNemar)
    if s.get("accuracy_p_value") is not None and s.get("n_discordant"):
        verdict_word = "SIGNIFICANT" if s.get("accuracy_significant") else "not significant (likely noise)"
        lines.append(f"              -> {verdict_word} "
                     f"(p={s['accuracy_p_value']:.3f}, {s['n_discordant']} case(s) flipped)")
    lines += [
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
    if s.get("flaky_cases"):
        lines.append("")
        lines.append(f"  flaky cases (non-unanimous across --trials): {len(s['flaky_cases'])}")
        for name in s["flaky_cases"]:
            lines.append(f"    - {name}")
    lines.append("")
    return "\n".join(lines)


def format_table(cmp: dict) -> str:
    """Human-readable comparison for the terminal."""
    a, b = cmp["a"], cmp["b"]
    lines = [
        f"\n{'='*78}",
        f"  {a['label']}  vs  {b['label']}",
        f"{'='*78}",
    ]
    # M-02: warn loudly when the two runs did not measure the same thing, so
    # nobody reads the per-case table as a like-for-like verdict.
    compat = cmp.get("compatibility", {})
    if compat.get("reasons"):
        header = ("NOT DIRECTLY COMPARABLE" if compat.get("comparable") is False
                  else "COMPARABILITY UNVERIFIED")
        lines.append(f"  ** {header} **")
        for reason in compat["reasons"]:
            lines.append(f"     - {reason}")
        if cmp.get("verdict", {}).get("suppressed"):
            lines.append("     (overall winner suppressed; per-case deltas below are on shared cases only. "
                         "Re-run `compare --force` to override.)")
        lines.append("")
    lines += [
        f"  {'case':30} {'A':>10} {'B':>10} {'A time':>8} {'B time':>8}",
        f"  {'-'*30} {'-'*10} {'-'*10} {'-'*8} {'-'*8}",
    ]
    for row in cmp["cases"]:
        # "PASS 2/3" for --trials runs - a majority verdict with dissenting
        # trials is a weaker claim than a unanimous one, and hiding that is
        # how flaky agents get oversold.
        def mark(v, trials):
            base = "-" if v is None else ("PASS" if v else "fail")
            return f"{base} {trials}" if trials else base
        t = lambda v: "-" if v is None else f"{v:.1f}s"                    # noqa: E731
        lines.append(
            f"  {row['case']:30} {mark(row['a_passed'], row.get('a_trials')):>10} "
            f"{mark(row['b_passed'], row.get('b_trials')):>10} "
            f"{t(row['a_duration_s']):>8} {t(row['b_duration_s']):>8}"
        )
    sa, sb = a["summary"], b["summary"]
    v = cmp["verdict"]
    _winner = lambda name: name if name else "(suppressed - not comparable)"  # noqa: E731

    def _rate(s):  # "50% [35-65%]" - point estimate with its 95% Wilson CI
        ci = s.get("pass_rate_ci")
        base = f"{s['pass_rate']:.0%}"
        return f"{base} [{ci[0]:.0%}-{ci[1]:.0%}]" if ci else base

    lines += [
        f"  {'-'*70}",
        f"  pass rate:     {_rate(sa)}  vs  {_rate(sb)}"
        f"   -> more accurate: {_winner(v['more_accurate'])}",
    ]
    # Significance of the accuracy delta (paired exact McNemar). Only meaningful
    # when a winner wasn't suppressed and there's a real accuracy gap.
    if not v.get("suppressed") and v.get("accuracy_p_value") is not None and v["more_accurate"] != "tie":
        p, n = v["accuracy_p_value"], v.get("n_discordant", 0)
        verdict_word = "SIGNIFICANT" if v.get("accuracy_significant") else "NOT significant (likely noise)"
        lines.append(f"                 accuracy delta: {verdict_word} "
                     f"(p={p:.3f}, {n} case(s) flipped)")
    lines += [
        f"  mean duration: {sa['mean_duration_s']:.1f}s vs {sb['mean_duration_s']:.1f}s"
        f"   -> faster: {_winner(v['faster'])}",
    ]
    if sa.get("clean_passes") is not None and sb.get("clean_passes") is not None:
        lines.append(
            f"  clean passes:  {sa['clean_passes']}/{sa['cases']} vs {sb['clean_passes']}/{sb['cases']}"
            f"   (passed with no off-target edits / clean exit)")
    # Efficiency: cost of each success (LoCoBench-Agent's "cheaper to get right").
    if sa.get("tokens_per_pass") and sb.get("tokens_per_pass"):
        lines.append(f"  tokens/pass:   {sa['tokens_per_pass']} vs {sb['tokens_per_pass']}"
                     f"   (tokens spent per passing case)")
    if sa.get("p95_duration_s") is not None and sb.get("p95_duration_s") is not None:
        lines.append(
            f"  p95 duration:  {sa['p95_duration_s']:.1f}s vs {sb['p95_duration_s']:.1f}s")
    if sa.get("flaky_cases") or sb.get("flaky_cases"):
        lines.append(
            f"  flaky cases:   {sa.get('flaky_cases', 0)}  vs  {sb.get('flaky_cases', 0)}"
            f"   (non-unanimous across --trials)")
    if v.get("cheaper"):
        ca = sa.get("total_cost_usd") or 0.0
        cb = sb.get("total_cost_usd") or 0.0
        lines.append(
            f"  cost:          ${ca:.2f}  vs  ${cb:.2f}   -> cheaper: {v['cheaper']}"
        )
    lines.append("")
    return "\n".join(lines)
