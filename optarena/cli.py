"""
optarena/cli.py
────────────
Command-line interface.

    optarena  (or: python -m optarena) run --scenario s1.json [--scenario s2.json]
    optarena  (or: python -m optarena) run --driver aider --model selfopt \
           --base-url http://localhost:11434 --name aider-selfopt [--cases a,b]
    optarena  (or: python -m optarena) compare <run_ref_a> <run_ref_b>
    optarena  (or: python -m optarena) list [runs|cases|drivers]
    optarena  (or: python -m optarena) serve [--port 8300]

Passing two --scenario (or --ab "driverA:driverB") to `run` executes both and
prints + saves the comparison automatically.
"""

from __future__ import annotations

import argparse
import os
import functools
import http.server
import sys
from pathlib import Path

from .cases import load_cases
from .compare import compare_runs, format_table, save_comparison
from .drivers import DRIVER_NAMES
from .runner import run_scenario
from .scenario import Backend, Scenario
from .store import list_runs, load_run, save_run

REPO_ROOT = Path(__file__).resolve().parents[1]


def _scenario_from_args(args, suffix: str = "") -> Scenario:
    backend = Backend(kind=args.kind, base_url=args.base_url,
                      model=args.model, api_key=args.api_key)
    name = args.name or f"{args.driver}-{args.model}{suffix}"
    return Scenario(
        name=name, driver=args.driver, backend=backend,
        cases=args.cases.split(",") if args.cases else None,
        timeout=args.timeout,
    )


def cmd_run(args) -> int:
    scenarios: list[Scenario] = [Scenario.from_file(p) for p in args.scenario or []]
    if args.driver:
        scenarios.append(_scenario_from_args(args))
    if not scenarios:
        print("Nothing to run: pass --scenario file.json or --driver …", file=sys.stderr)
        return 2

    records = []
    for sc in scenarios:
        rec = run_scenario(sc)
        path = save_run(rec)
        s = rec.summary
        print(f"  -> {s['passed']}/{s['cases']} passed "
              f"(mean {s['mean_duration_s']}s) — saved {path.name}")
        records.append(rec)

    if len(records) >= 2:
        cmp = compare_runs(records[0].to_dict(), records[1].to_dict())
        print(format_table(cmp))
        print(f"  comparison saved: {save_comparison(cmp)}")
    return 0 if all(r.summary["failed"] == 0 for r in records) else 1


def cmd_compare(args) -> int:
    cmp = compare_runs(load_run(args.run_a), load_run(args.run_b))
    print(format_table(cmp))
    print(f"  comparison saved: {save_comparison(cmp)}")
    return 0


def cmd_list(args) -> int:
    what = args.what
    if what == "cases":
        for c in load_cases():
            print(f"  {c['name']:28} {c.get('description', '')}")
    elif what == "drivers":
        for d in DRIVER_NAMES:
            print(f"  {d}")
    else:
        for r in list_runs():
            s = r.get("summary", {})
            b = r.get("backend", {})
            print(f"  {r['run_id']:44} {r.get('driver', ''):12} "
                  f"{b.get('model', ''):14} {s.get('passed', '?')}/{s.get('cases', '?')} "
                  f"({s.get('mean_duration_s', '?')}s avg)")
    return 0


def cmd_serve(args) -> int:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(REPO_ROOT))
    url = f"http://localhost:{args.port}/dashboard/"
    print(f"OptArena dashboard: {url}  (Ctrl+C to stop)")
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="optarena", description="OptArena — test & compare AI coding tools")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run one or more scenarios")
    p_run.add_argument("--scenario", action="append", help="scenario JSON file")
    p_run.add_argument("--driver", choices=DRIVER_NAMES, help="inline scenario: driver")
    p_run.add_argument("--name", help="inline scenario name")
    p_run.add_argument("--kind", default="ollama", choices=["ollama", "openai"])
    p_run.add_argument("--base-url", default=os.environ.get("OPTARENA_BASE_URL", "http://localhost:11434"))
    p_run.add_argument("--model", default=os.environ.get("OPTARENA_MODEL", "llama3.2"))
    p_run.add_argument("--api-key", default="optarena")
    p_run.add_argument("--cases", help="comma-separated case names (default all)")
    p_run.add_argument("--timeout", type=int, help="per-case timeout override (s)")
    p_run.set_defaults(fn=cmd_run)

    p_cmp = sub.add_parser("compare", help="compare two saved runs")
    p_cmp.add_argument("run_a")
    p_cmp.add_argument("run_b")
    p_cmp.set_defaults(fn=cmd_compare)

    p_list = sub.add_parser("list", help="list runs / cases / drivers")
    p_list.add_argument("what", nargs="?", default="runs",
                        choices=["runs", "cases", "drivers"])
    p_list.set_defaults(fn=cmd_list)

    p_serve = sub.add_parser("serve", help="serve the results dashboard")
    p_serve.add_argument("--port", type=int, default=8300)
    p_serve.set_defaults(fn=cmd_serve)

    args = parser.parse_args(argv)
    return args.fn(args)


def main_exit() -> None:
    """Console-script entry point (sys.exit wrapper around main)."""
    sys.exit(main())
