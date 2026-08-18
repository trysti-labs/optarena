"""
optarena/report.py
──────────────────
Turn a saved run into the report artifacts CI systems already understand, so an
OptArena run drops into GitHub Actions / GitLab / Jenkins summaries the same way
a normal test suite does - instead of only a terminal table + JSON.

- ``to_junit_xml``  - JUnit XML (the lingua franca of CI test reporting): one
  ``<testcase>`` per case, ``<failure>``/``<error>`` on the ones that didn't
  pass, wall time per case. Consumed natively by GitHub Actions test reporters,
  GitLab ``artifacts:reports:junit``, Jenkins, Buildkite, etc.
- ``to_html``       - a single self-contained HTML file (inline CSS, no network,
  no build step) that can be published as a CI artifact and opened directly.
- ``to_sarif``      - SARIF 2.1.0 for the security-scan findings (see
  ``security.py``), so GitHub code-scanning shows them inline on the PR.

All stdlib-only, matching the rest of the core.
"""

from __future__ import annotations

import json
from xml.sax.saxutils import escape, quoteattr


def _case_status(c: dict) -> str:
    if c.get("passed"):
        return "pass"
    return "error" if c.get("error") else "fail"


def to_junit_xml(run: dict) -> str:
    """JUnit XML for one saved run. ``failures`` = oracle-failed cases,
    ``errors`` = infrastructure errors (driver crash/timeout), matching the
    JUnit distinction consumers rely on."""
    cases = run.get("cases", [])
    scenario = run.get("scenario", {})
    backend = scenario.get("backend", {})
    classname = f"{scenario.get('driver', 'optarena')}.{backend.get('model', '')}".rstrip(".")
    n_err = sum(1 for c in cases if c.get("error"))
    n_fail = sum(1 for c in cases if not c.get("passed") and not c.get("error"))
    total_time = sum(c.get("duration_s") or 0 for c in cases)
    suite_name = scenario.get("name", run.get("run_id", "optarena"))

    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append(
        f'<testsuites name="optarena" tests="{len(cases)}" '
        f'failures="{n_fail}" errors="{n_err}" time="{total_time:.2f}">'
    )
    lines.append(
        f'  <testsuite name={quoteattr(suite_name)} tests="{len(cases)}" '
        f'failures="{n_fail}" errors="{n_err}" time="{total_time:.2f}" '
        f'timestamp={quoteattr(run.get("started_at", ""))}>'
    )
    for c in cases:
        tc = (f'    <testcase name={quoteattr(c["name"])} '
              f'classname={quoteattr(classname)} time="{c.get("duration_s") or 0:.2f}"')
        status = _case_status(c)
        if status == "pass":
            lines.append(tc + "/>")
            continue
        lines.append(tc + ">")
        if status == "error":
            msg = c.get("error") or "error"
            lines.append(f'      <error message={quoteattr(msg[:200])}>{escape(msg)}</error>')
        else:
            failures = c.get("failures") or ["failed"]
            msg = failures[0]
            body = "\n".join(failures)
            lines.append(f'      <failure message={quoteattr(msg[:200])}>{escape(body)}</failure>')
        # Surface the sandboxed test output as system-out (proof of what ran).
        out = (c.get("extra", {}).get("oracle", {}) or {}).get("output")
        if out:
            lines.append(f"      <system-out>{escape(out[:2000])}</system-out>")
        lines.append("    </testcase>")
    lines.append("  </testsuite>")
    lines.append("</testsuites>")
    return "\n".join(lines) + "\n"


_HTML_CSS = """
:root{--bg:#0c0c18;--card:#12122200;--text:#e2e8f0;--muted:#8892a4;--green:#22c55e;
--red:#ef4444;--accent:#7c5cfc;--border:rgba(255,255,255,.1)}
@media(prefers-color-scheme:light){:root{--bg:#f8f8fc;--text:#14141c;--muted:#52526b;--border:rgba(17,17,34,.1)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:32px;max-width:1100px;margin:0 auto}
h1{font-size:1.5rem;margin:0 0 4px}.sub{color:var(--muted);margin-bottom:24px;font-size:.9rem}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px}
.tile{border:1px solid var(--border);border-radius:8px;padding:14px 16px}
.tile .l{color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.08em}
.tile .v{font-size:1.5rem;font-weight:700;margin-top:6px}
table{width:100%;border-collapse:collapse;font-size:.875rem}
th{text-align:left;padding:8px 10px;color:var(--muted);font-size:.72rem;text-transform:uppercase;
letter-spacing:.07em;border-bottom:1px solid var(--border)}
td{padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:top}
.pass{color:var(--green);font-weight:600}.fail{color:var(--red);font-weight:600}
.det{color:var(--muted);font-size:.8rem;max-width:520px}
.badge{color:var(--red);font-family:monospace;font-size:.72rem}
footer{color:var(--muted);font-size:.8rem;margin-top:28px}
"""


def to_html(run: dict) -> str:
    """A single self-contained HTML report for one run - inline CSS, no network,
    publishable as a CI artifact and openable directly."""
    s = run.get("summary", {})
    scenario = run.get("scenario", {})
    backend = scenario.get("backend", {})
    ci = s.get("pass_rate_ci")
    ci_txt = f" &nbsp;95% CI [{ci[0]:.0%}–{ci[1]:.0%}]" if ci else ""

    rows = []
    for c in run.get("cases", []):
        status = _case_status(c)
        cls = "pass" if status == "pass" else "fail"
        label = "PASS" if status == "pass" else ("ERROR" if status == "error" else "FAIL")
        # A-16: truncate THEN escape. The other order can cut an entity in
        # half ("&lt;" -> "&l"), which renders as literal text in the report.
        detail = "" if status == "pass" else escape(
            (c.get("error") or "; ".join(c.get("failures", [])[:1]) or "")[:400])
        traj = (c.get("extra", {}).get("oracle", {}) or {}).get("trajectory", {}) or {}
        off = traj.get("off_target_count", 0)
        badge = f' <span class="badge">⚠ {off} off-target</span>' if off else ""
        rows.append(
            f'<tr><td>{escape(c["name"])}{badge}</td>'
            f'<td class="{cls}">{label}</td>'
            f'<td>{c.get("duration_s") or 0:.1f}s</td>'
            f'<td class="det">{detail}</td></tr>'
        )

    def tile(label, value):
        return f'<div class="tile"><div class="l">{label}</div><div class="v">{value}</div></div>'

    clean = f'{s["clean_passes"]}/{s["cases"]}' if s.get("clean_passes") is not None else "-"
    cost = f'${s["total_cost_usd"]:.2f}' if s.get("total_cost_usd") is not None else "-"
    if s.get("unpriced_cases"):
        # P2-05: a total built from only SOME cases' known prices must not
        # read as complete - "$0.42" implies every case is accounted for.
        cost += f" (+{s['unpriced_cases']} case(s) unpriced)"
    # P2-04: raw "Pass rate" above is always over every case - when the
    # driver structurally couldn't attempt some of them (no file tools, a
    # case needing >1 file), an extra tile states the eligible rate and
    # denominator explicitly rather than leaving that only in the raw JSON.
    # Only rendered when it applies, same convention as the cost tile above.
    extra_tiles = ""
    if s.get("capability_excluded_cases"):
        eligible_n = s["cases"] - s["capability_excluded_cases"]
        extra_tiles += tile(
            "Eligible pass rate",
            f"{s['eligible_pass_rate']:.0%} ({eligible_n}/{s['cases']} case(s) "
            f"this driver could attempt)")
    if s.get("infrastructure_errors"):
        non_infra_n = s["cases"] - s["infrastructure_errors"]
        extra_tiles += tile(
            "Adjusted pass rate",
            f"{s['adjusted_pass_rate']:.0%} ({non_infra_n}/{s['cases']} case(s) "
            f"with a real verdict)")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OptArena report - {escape(scenario.get('name', ''))}</title>
<style>{_HTML_CSS}</style></head><body>
<h1>OptArena report</h1>
<div class="sub">{escape(scenario.get('name', ''))} &middot; driver <b>{escape(scenario.get('driver', ''))}</b>
 &middot; {escape(backend.get('model', ''))} &middot; {escape(run.get('run_id', ''))}
 &middot; {escape(run.get('started_at', ''))}</div>
<div class="tiles">
{tile("Pass rate", f"{s.get('pass_rate', 0):.0%}{ci_txt}")}
{tile("Passed", f"{s.get('passed', 0)}/{s.get('cases', 0)}")}
{tile("Clean passes", clean)}
{tile("Mean time", f"{s.get('mean_duration_s', 0):.1f}s")}
{tile("Cost", cost)}
{extra_tiles}
</div>
<table><thead><tr><th>case</th><th>result</th><th>time</th><th>detail</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<footer>Generated by <b>optarena report</b> &middot; Apache-2.0</footer>
</body></html>
"""


def to_sarif(run: dict) -> str:
    """SARIF 2.1.0 for the security-scan findings attached to a run's cases
    (``extra.security``, produced by ``security.scan_workspace``). Empty-but-valid
    when no scan ran, so ``optarena report --format sarif`` always yields a file
    GitHub code-scanning will accept."""
    rules: dict[str, dict] = {}
    results = []
    for c in run.get("cases", []):
        for f in (c.get("extra", {}).get("security", {}) or {}).get("findings", []):
            rule_id = f["rule"]
            rules.setdefault(rule_id, {
                "id": rule_id,
                "name": rule_id,
                "shortDescription": {"text": f.get("title", rule_id)},
                "defaultConfiguration": {"level": f.get("level", "warning")},
            })
            results.append({
                "ruleId": rule_id,
                "level": f.get("level", "warning"),
                "message": {"text": f"[{c['name']}] {f.get('message', f.get('title', rule_id))}"},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": f.get("file", c["name"])},
                        "region": {"startLine": max(1, int(f.get("line", 1)))},
                    }
                }],
            })
    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "OptArena",
                "informationUri": "https://github.com/trysti-labs/optarena",
                "rules": list(rules.values()),
            }},
            "results": results,
        }],
    }
    return json.dumps(sarif, indent=2) + "\n"


def to_markdown_summary(run: dict) -> str:
    """A markdown summary suitable for GitHub Actions job summary or similar CI
    contexts. Includes overall pass rate, breakdown by language, category, and
    a list of failing cases."""
    s = run.get("summary", {})
    scenario = run.get("scenario", {})
    backend = scenario.get("backend", {})
    cases = run.get("cases", [])

    lines = []
    lines.append("# OptArena Report")
    lines.append("")
    lines.append(f"**Scenario:** {scenario.get('name', 'N/A')}")
    lines.append(f"**Driver:** {scenario.get('driver', 'N/A')}")
    lines.append(f"**Model:** {backend.get('model', 'N/A')}")
    lines.append(f"**Run ID:** `{run.get('run_id', 'N/A')}`")
    lines.append("")

    # Overall pass rate
    pass_rate = s.get("pass_rate", 0)
    passed = s.get("passed", 0)
    total = s.get("cases", 0)
    ci = s.get("pass_rate_ci")
    ci_str = f" (95% CI: {ci[0]:.0%}–{ci[1]:.0%})" if ci else ""
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Pass Rate | {pass_rate:.0%}{ci_str} |")
    lines.append(f"| Passed | {passed}/{total} |")
    if s.get("clean_passes") is not None:
        lines.append(f"| Clean Passes | {s.get('clean_passes')}/{total} |")
    lines.append(f"| Mean Duration | {s.get('mean_duration_s', 0):.1f}s |")
    if s.get("total_cost_usd") is not None:
        cost = f"${s.get('total_cost_usd'):.2f}"
        if s.get("unpriced_cases"):
            cost += f" (+{s.get('unpriced_cases')} unpriced)"
        lines.append(f"| Total Cost | {cost} |")
    lines.append("")

    # Breakdown by language
    by_language = {}
    for c in cases:
        lang = c.get("extra", {}).get("language") or c.get("language", "Unknown")
        if lang not in by_language:
            by_language[lang] = {"passed": 0, "total": 0}
        by_language[lang]["total"] += 1
        if c.get("passed"):
            by_language[lang]["passed"] += 1

    if by_language:
        lines.append("## By Language")
        lines.append("")
        lines.append("| Language | Passed | Total | Pass Rate |")
        lines.append("|----------|--------|-------|-----------|")
        for lang in sorted(by_language.keys()):
            stats = by_language[lang]
            rate = stats["passed"] / stats["total"] if stats["total"] else 0
            lines.append(f"| {lang} | {stats['passed']} | {stats['total']} | {rate:.0%} |")
        lines.append("")

    # Breakdown by task type (category)
    by_task_type = {}
    for c in cases:
        task_type = c.get("extra", {}).get("task_type") or c.get("task_type", "Unknown")
        if task_type not in by_task_type:
            by_task_type[task_type] = {"passed": 0, "total": 0}
        by_task_type[task_type]["total"] += 1
        if c.get("passed"):
            by_task_type[task_type]["passed"] += 1

    if by_task_type:
        lines.append("## By Task Type")
        lines.append("")
        lines.append("| Task Type | Passed | Total | Pass Rate |")
        lines.append("|-----------|--------|-------|-----------|")
        for task_type in sorted(by_task_type.keys()):
            stats = by_task_type[task_type]
            rate = stats["passed"] / stats["total"] if stats["total"] else 0
            lines.append(f"| {task_type} | {stats['passed']} | {stats['total']} | {rate:.0%} |")
        lines.append("")

    # Failing cases
    failing_cases = [c for c in cases if not c.get("passed")]
    if failing_cases:
        lines.append("## Failing Cases")
        lines.append("")
        for c in failing_cases:
            status = _case_status(c)
            label = "ERROR" if status == "error" else "FAIL"
            lines.append(f"- **{c['name']}** ({label})")
            if c.get("error"):
                detail = c.get("error")[:100]
                lines.append(f"  - Error: {detail}")
            elif c.get("failures"):
                detail = c.get("failures")[0][:100]
                lines.append(f"  - {detail}")
        lines.append("")

    lines.append("*Generated by optarena report* • Apache-2.0")
    return "\n".join(lines) + "\n"
