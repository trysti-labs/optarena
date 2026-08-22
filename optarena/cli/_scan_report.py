"""Standalone security scanning (`optarena scan`) and CI report-artifact
generation from a saved run (`optarena report`)."""

from __future__ import annotations

import sys
from pathlib import Path

from .. import store


def cmd_scan(args) -> int:
    """Standalone static security scan of a directory (the same rules the
    --security-scan run flag applies to an agent's changed files)."""
    from ..security import _SCAN_MAX_FILES, scan_workspace

    root = Path(args.dir)
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2
    # P1-06: skip symlinks at enumeration too (scan_workspace re-checks this
    # itself, but there's no reason to even stat-follow them here first),
    # and stop early rather than fully materializing an unbounded directory
    # tree before scan_workspace gets a chance to apply its own file cap.
    files = []
    for p in root.rglob("*"):
        if len(files) >= _SCAN_MAX_FILES:
            break
        if p.is_symlink() or not p.is_file():
            continue
        files.append(str(p.relative_to(root)))
    res = scan_workspace(files, root)
    if not res["total"]:
        print(f"  no findings in {root} ({len(files)} file(s) scanned)")
        return 0
    c = res["counts"]
    print(f"  {res['total']} finding(s) in {root}: "
          f"{c.get('error', 0)} error, {c.get('warning', 0)} warning, {c.get('note', 0)} note")
    for f in res["findings"]:
        print(f"    [{f['level']:7}] {f['title']}")
        print(f"              {f['file']}:{f['line']}  {f['snippet']}")
    # Exit non-zero when there's an error-level finding, so it's usable as a gate.
    return 1 if c.get("error", 0) else 0


def cmd_report(args) -> int:
    """Emit CI-native report artifacts (JUnit XML / self-contained HTML / SARIF)
    from a saved run, so an OptArena run drops into GitHub Actions/GitLab CI the
    same way a normal test suite does."""
    from .. import report as _report

    try:
        run = store.load_run(args.run_ref)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    formats = ["junit", "html", "sarif", "markdown"] if args.format == "all" else [args.format]
    renderers = {"junit": (_report.to_junit_xml, "junit.xml"),
                 "html": (_report.to_html, "report.html"),
                 "sarif": (_report.to_sarif, "results.sarif"),
                 "markdown": (_report.to_markdown_summary, "summary.md")}

    # A-27: --out-dir says "directory", --out says "file" - no guessing.
    # `--out` alone used to be inferred as a file or a directory from the
    # format count, a trailing slash, and whether the path already existed, so
    # `--format junit --out build/reports` wrote a FILE named `reports` when
    # that directory didn't exist yet and a file INSIDE it when it did.
    if args.out and args.out_dir:
        print("error: pass --out (a file) or --out-dir (a directory), not both", file=sys.stderr)
        return 2
    if args.out and len(formats) > 1:
        print(f"error: --out names a single file but --format {args.format} produces "
              f"{len(formats)} artifacts - use --out-dir", file=sys.stderr)
        return 2

    written = []
    if args.out:
        dest = Path(args.out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(renderers[formats[0]][0](run), encoding="utf-8")
        written.append(dest)
    else:
        out = Path(args.out_dir) if args.out_dir else (store.RESULTS_DIR / "reports" / run["run_id"])
        out.mkdir(parents=True, exist_ok=True)
        for fmt in formats:
            fn, default_name = renderers[fmt]
            dest = out / default_name
            dest.write_text(fn(run), encoding="utf-8")
            written.append(dest)
    for p in written:
        print(f"  wrote {p}")
    return 0
