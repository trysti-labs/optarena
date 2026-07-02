"""
shared/result.py
────────────────
TestResult dataclass and report generator (JSON + HTML).
"""

from __future__ import annotations

import datetime
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class TestResult:
    name:        str
    tool:        str
    api_mode:    str
    passed:      bool = False
    files:       list[str] = field(default_factory=list)
    errors:      list[str] = field(default_factory=list)
    warnings:    list[str] = field(default_factory=list)
    response_log: Optional[str] = None
    duration_s:  float = 0.0
    _start:      float = field(default_factory=time.monotonic, repr=False, compare=False)

    def start(self) -> "TestResult":
        self._start = time.monotonic()
        return self

    def finish(self) -> "TestResult":
        self.duration_s = round(time.monotonic() - self._start, 1)
        return self

    def fail(self, msg: str) -> "TestResult":
        self.errors.append(msg)
        self.passed = False
        return self

    def warn(self, msg: str) -> "TestResult":
        self.warnings.append(msg)
        return self

    def pass_(self) -> "TestResult":
        self.passed = True
        return self

    def to_dict(self) -> dict:
        return {
            "name":         self.name,
            "tool":         self.tool,
            "api_mode":     self.api_mode,
            "passed":       self.passed,
            "duration_s":   self.duration_s,
            "files":        self.files,
            "errors":       self.errors,
            "warnings":     self.warnings,
            "response_log": self.response_log,
        }

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        parts = [f"[{status}] {self.tool}/{self.name} api={self.api_mode} ({self.duration_s}s)"]
        if self.files:
            parts.append(f"  files: {self.files}")
        for w in self.warnings:
            parts.append(f"  WARN: {w}")
        for e in self.errors:
            parts.append(f"  ERR:  {e}")
        return "\n".join(parts)


# ── HTML report ───────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>SelfOpt Tool Test Report</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 20px; background: #f8f8f8; }}
  h1 {{ color: #333; }}
  .summary {{ display: flex; gap: 20px; margin-bottom: 24px; }}
  .card {{ background: white; border-radius: 8px; padding: 16px 24px;
           box-shadow: 0 1px 4px rgba(0,0,0,.1); min-width: 100px; text-align: center; }}
  .card .num {{ font-size: 2rem; font-weight: bold; }}
  .pass .num {{ color: #22c55e; }}
  .fail .num {{ color: #ef4444; }}
  table {{ border-collapse: collapse; width: 100%; background: white;
           border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,.1); overflow: hidden; }}
  th {{ background: #374151; color: white; padding: 10px 16px; text-align: left; }}
  td {{ padding: 10px 16px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  .badge {{ display: inline-block; border-radius: 4px; padding: 2px 8px;
            font-size: .8rem; font-weight: bold; color: white; }}
  .badge-pass {{ background: #22c55e; }}
  .badge-fail {{ background: #ef4444; }}
  pre {{ background: #f1f5f9; border-radius: 4px; padding: 8px; font-size: .8rem;
         max-height: 200px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; }}
  .ts {{ color: #6b7280; font-size: .85rem; }}
</style>
</head>
<body>
<h1>SelfOpt Tool Test Report</h1>
<p class="ts">Generated: {timestamp}</p>
<div class="summary">
  <div class="card pass"><div class="num">{n_pass}</div><div>Passed</div></div>
  <div class="card fail"><div class="num">{n_fail}</div><div>Failed</div></div>
  <div class="card"><div class="num">{n_total}</div><div>Total</div></div>
</div>
<table>
<thead><tr>
  <th>Status</th><th>Tool</th><th>Test</th><th>API</th><th>Duration</th><th>Details</th>
</tr></thead>
<tbody>
{rows}
</tbody>
</table>
</body>
</html>
"""

_ROW_TEMPLATE = """<tr>
  <td><span class="badge badge-{badge}">{status}</span></td>
  <td>{tool}</td>
  <td>{name}</td>
  <td>{api}</td>
  <td>{dur}s</td>
  <td>
    {files_html}
    {errors_html}
    {warnings_html}
  </td>
</tr>"""


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_report(results: list[TestResult], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # JSON
    report = {
        "generated": datetime.datetime.utcnow().isoformat() + "Z",
        "summary": {
            "total":  len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
        },
        "results": [r.to_dict() for r in results],
    }
    json_path = out_dir / "report.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n[report] JSON → {json_path}")

    # HTML
    n_pass  = report["summary"]["passed"]
    n_fail  = report["summary"]["failed"]
    n_total = report["summary"]["total"]
    rows: list[str] = []

    for r in results:
        files_html    = f"<b>Files:</b> {_esc(', '.join(r.files))}<br>" if r.files else ""
        errors_html   = "".join(f"<pre>ERR: {_esc(e)}</pre>" for e in r.errors)
        warnings_html = "".join(f"<pre>WARN: {_esc(w)}</pre>" for w in r.warnings)
        rows.append(_ROW_TEMPLATE.format(
            badge       = "pass" if r.passed else "fail",
            status      = "PASS" if r.passed else "FAIL",
            tool        = _esc(r.tool),
            name        = _esc(r.name),
            api         = _esc(r.api_mode),
            dur         = r.duration_s,
            files_html  = files_html,
            errors_html = errors_html,
            warnings_html = warnings_html,
        ))

    html = _HTML_TEMPLATE.format(
        timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        n_pass    = n_pass,
        n_fail    = n_fail,
        n_total   = n_total,
        rows      = "\n".join(rows),
    )
    html_path = out_dir / "report.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"[report] HTML → {html_path}")
