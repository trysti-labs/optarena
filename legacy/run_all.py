#!/usr/bin/env python3
"""
tests/tools/run_all.py
──────────────────────
Run all VS Code extension integration tests across all tools.

Usage:
    python tests/tools/run_all.py [options]

Options:
    --api     ollama|openai    API mode (default: ollama)
    --selfopt URL              SelfOpt base URL (default: http://localhost:8001)
    --tools   TOOL [TOOL ...]  Restrict to specific tools: cline roo_cline continue
    --test    NAME [NAME ...]  Restrict to specific test-case names (applied to all tools)
    --timeout INT              Override timeout for all tests
    --output  DIR              Top-level output dir (default: tests/tools/results/<ts>)
    --workspace DIR            Shared workspace base dir
    --cdp-port INT             VS Code CDP port (default: 9222)
    --no-selfopt-check         Skip SelfOpt health check
"""

from __future__ import annotations

import argparse
import sys
import time
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.tools.shared.result import TestResult, write_report
from tests.tools.shared.selfopt import require_selfopt

try:
    from playwright.sync_api import sync_playwright  # noqa: F401 - availability check
except ImportError:
    print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)


TOOL_NAMES = ["cline", "roo_cline", "continue"]


def _get_runner(tool: str):
    if tool == "cline":
        import tests.tools.cline.run_tests as mod
        import tests.tools.cline.test_cases as tc
    elif tool == "roo_cline":
        import tests.tools.roo_cline.run_tests as mod
        import tests.tools.roo_cline.test_cases as tc
    elif tool == "continue":
        import tests.tools.continue_ext.run_tests as mod
        import tests.tools.continue_ext.test_cases as tc
    else:
        raise ValueError(f"Unknown tool: {tool!r}")
    return mod.run_one, tc.all_cases, tc.load_case


def main() -> None:
    parser = argparse.ArgumentParser(description="Run all VS Code extension integration tests")
    parser.add_argument("--api",       default="ollama", choices=["ollama", "openai"])
    parser.add_argument("--selfopt",   default="http://localhost:8001")
    parser.add_argument("--tools",     nargs="*", choices=TOOL_NAMES + ["all"], default=["all"])
    parser.add_argument("--test",      nargs="*")
    parser.add_argument("--timeout",   type=int, default=None)
    parser.add_argument("--output",    default=None)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--cdp-port",  type=int, default=9222)
    parser.add_argument("--no-selfopt-check", action="store_true")
    args = parser.parse_args()

    if not args.no_selfopt_check:
        try:
            require_selfopt(args.selfopt)
            print(f"  [main] SelfOpt OK at {args.selfopt}")
        except RuntimeError as e:
            print(f"ERROR: {e}")
            sys.exit(1)

    tools = TOOL_NAMES if "all" in (args.tools or ["all"]) else (args.tools or TOOL_NAMES)
    print(f"  [main] Tools: {tools}, API: {args.api}, SelfOpt: {args.selfopt}")

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.output) if args.output else (
        Path(__file__).parent / "results" / ts
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    ws_base = Path(args.workspace) if args.workspace else Path(tempfile.mkdtemp(prefix="tools_test_"))
    ws_base.mkdir(parents=True, exist_ok=True)

    all_results: list[TestResult] = []

    for tool in tools:
        print(f"\n{'#'*60}")
        print(f"  TOOL: {tool.upper()}")
        print(f"{'#'*60}")

        try:
            run_one, get_all_cases, get_case = _get_runner(tool)
        except Exception as e:
            print(f"  [main] Skipping {tool}: {e}")
            continue

        if args.test:
            try:
                cases = [get_case(n) for n in args.test]
            except Exception as e:
                print(f"  [main] Could not load test cases for {tool}: {e}")
                continue
        else:
            try:
                cases = get_all_cases()
            except Exception as e:
                print(f"  [main] Could not discover test cases for {tool}: {e}")
                continue

        if not cases:
            print(f"  [main] No test cases for {tool}; skipping")
            continue

        ws = ws_base / tool
        ws.mkdir(exist_ok=True)

        for case in cases:
            r = run_one(
                case=case,
                api_mode=args.api,
                selfopt_url=args.selfopt,
                workspace=ws,
                cdp_port=args.cdp_port,
                timeout_override=args.timeout,
            )
            all_results.append(r)

    # Write combined report
    report_path = write_report(all_results, out_dir)
    passed = sum(1 for r in all_results if r.status == "pass")
    failed = sum(1 for r in all_results if r.status == "fail")

    print(f"\n{'='*60}")
    print(f"  COMBINED REPORT: {report_path}")
    print(f"  {passed}/{len(all_results)} passed, {failed} failed")
    print(f"{'='*60}\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
