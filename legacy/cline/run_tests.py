#!/usr/bin/env python3
"""
tests/tools/cline/run_tests.py
───────────────────────────────
Entry point for automated Cline tests.

Usage:
    python -m tests.tools.cline.run_tests [options]
    # or from workspace root:
    python tests/tools/cline/run_tests.py [options]

Options:
    --api     ollama|openai    API compatibility mode (default: ollama)
    --selfopt URL              SelfOpt base URL (default: http://localhost:8001)
    --test    NAME [NAME ...]  Run only specific test cases (default: all)
    --timeout INT              Override timeout for all tests (seconds)
    --output  DIR              Output directory for results (default: tests/tools/results)
    --workspace DIR            Temp workspace directory (default: system temp)
    --cdp-port INT             VS Code CDP debug port (default: 9222)
"""

from __future__ import annotations

import argparse
import sys
import time
import tempfile
from pathlib import Path

# Ensure repo root is on sys.path when run directly
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.tools.cline.test_cases import all_cases, load_case
from tests.tools.cline.runner import configure, open_panel, inject_prompt
from tests.tools.shared.vscode_utils import (
    cdp_alive, launch_vscode, kill_vscode, cdp_connect,
    check_extension_installed,
)
from tests.tools.shared.workspace import setup_workspace, snapshot
from tests.tools.shared.approvals import auto_approve_loop
from tests.tools.shared.result import TestResult, write_report
from tests.tools.shared.selfopt import require_selfopt

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)


def run_one(
    case: dict,
    api_mode: str,
    selfopt_url: str,
    workspace: Path,
    cdp_port: int,
    timeout_override: int | None,
) -> TestResult:
    """Run a single Cline test case and return a TestResult."""
    result = TestResult(name=case["name"], tool="cline", api_mode=api_mode)
    result.start()

    timeout = timeout_override or case.get("timeout", 120)
    prompts = case.get("prompts", [])
    setup_files = case.get("setup_files", {})
    expected_spec = case.get("expected_files", [])

    # ── 1. Prepare workspace ──────────────────────────────────────────────────
    setup_workspace(workspace, setup_files)
    before = snapshot(workspace)
    print(f"\n{'='*60}")
    print(f"  TEST: {case['name']}")
    print(f"  API:  {api_mode} → {selfopt_url}")
    print(f"  WS:   {workspace}")
    print(f"{'='*60}")

    # ── 2. Kill any stale instance, configure Cline, launch VS Code ──────────
    # Always kill whatever is on the port first so globalState can be written
    # while VS Code is stopped, then do a fresh isolated launch.
    if cdp_alive(cdp_port):
        print(f"  [run] stale VS Code on :{cdp_port} - killing before configure")
        kill_vscode(cdp_port)

    try:
        warnings = configure(api_mode, selfopt_url)
        for w in warnings:
            result.warn(w)
    except Exception as e:
        print(f"  [run] FAIL configure(): {e}")
        result.fail(f"configure() failed: {e}")
        result.finish()
        return result

    try:
        launch_vscode(workspace, cdp_port)
    except RuntimeError as e:
        print(f"  [run] FAIL launch_vscode(): {e}")
        result.fail(str(e))
        result.finish()
        return result

    print("  [run] VS Code started, CDP live")
    time.sleep(4)  # Let VS Code fully initialise

    # ── 3. Playwright: connect, open Cline panel, inject prompts ─────────────
    with sync_playwright() as pw:
        browser = None
        try:
            try:
                browser, ctx, page = cdp_connect(pw, cdp_port)
            except Exception as e:
                print(f"  [run] FAIL cdp_connect(): {e}")
                result.fail(f"CDP connect failed: {e}")
                return result

            # Warn (don't fail) if extension not found in extensions dir
            if not check_extension_installed("saoudrizwan.claude-dev"):
                print("  [run] WARN: Cline extension not found in extensions dir - proceeding anyway")
                result.warn("Cline extension not detected in extensions directory")

            # Open Cline panel
            if not open_panel(page, ctx):
                result.warn("Could not confirm Cline panel opened; continuing anyway")
            time.sleep(2)

            is_multi = len(prompts) > 1

            for i, prompt_text in enumerate(prompts):
                print(f"  [run] injecting prompt {i+1}/{len(prompts)}: {prompt_text[:60]}...")

                ok, msg = inject_prompt(page, prompt_text)
                if not ok:
                    print(f"  [run] FAIL inject_prompt turn {i+1}: {msg}")
                    result.fail(f"inject_prompt failed (turn {i+1}): {msg}")
                    return result

                # For multi-turn, run approve loop between turns (shorter timeout)
                if is_multi and i < len(prompts) - 1:
                    turn_timeout = min(timeout // len(prompts), 90)
                    passed, created, err = auto_approve_loop(
                        ctx, workspace, before, [],
                        timeout=turn_timeout, poll_interval=2.0, idle_done_timeout=10,
                    )
                    if err and "timed out" not in err.lower():
                        print(f"  [run] FAIL mid-turn approve loop: {err}")
                        result.fail(f"Error during turn {i+1}: {err}")
                        return result
                    before = snapshot(workspace)

            # ── 4. Approve loop for final (or only) turn ──────────────────────
            passed, created, err = auto_approve_loop(
                ctx, workspace, before, expected_spec,
                timeout=timeout, poll_interval=2.0, idle_done_timeout=15,
            )

            if passed:
                result.pass_()
                print(f"  [run] PASS - files: {[f.name for f in created]}")
            else:
                result.fail(err or "Expected files not created")
                print(f"  [run] FAIL - {err}")

        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            print(f"  [run] FAIL unexpected: {exc}\n{tb}")
            result.fail(f"Unexpected error: {exc}\n{tb}")

        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

    # ── 5. Cleanup ────────────────────────────────────────────────────────────
    kill_vscode(cdp_port)
    result.finish()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run automated Cline integration tests"
    )
    parser.add_argument("--api",       default="ollama",
                        choices=["ollama", "openai"],
                        help="API compatibility mode (default: ollama)")
    parser.add_argument("--selfopt",   default="http://localhost:8001",
                        help="SelfOpt base URL")
    parser.add_argument("--test",      nargs="*",
                        help="Names of specific test cases to run (default: all)")
    parser.add_argument("--timeout",   type=int, default=None,
                        help="Override timeout for all tests (seconds)")
    parser.add_argument("--output",    default=None,
                        help="Output dir for JSON/HTML report")
    parser.add_argument("--workspace", default=None,
                        help="Temp workspace directory (default: system temp/cline_test_<ts>)")
    parser.add_argument("--cdp-port",  type=int, default=9222,
                        help="VS Code CDP debug port (default: 9222)")
    parser.add_argument("--no-selfopt-check", action="store_true",
                        help="Skip the SelfOpt server health check")
    args = parser.parse_args()

    # Validate SelfOpt
    if not args.no_selfopt_check:
        try:
            require_selfopt(args.selfopt)
            print(f"  [main] SelfOpt OK at {args.selfopt}")
        except RuntimeError as e:
            print(f"ERROR: {e}")
            sys.exit(1)

    # Load test cases
    if args.test:
        cases = [load_case(n) for n in args.test]
    else:
        cases = all_cases()

    if not cases:
        print("No test cases found.")
        sys.exit(0)

    print(f"  [main] Running {len(cases)} test case(s) with API={args.api}")

    # Output dir
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.output) if args.output else (
        Path(__file__).parents[1] / "results" / f"cline_{ts}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    # Workspace dir
    if args.workspace:
        ws = Path(args.workspace)
        ws.mkdir(parents=True, exist_ok=True)
    else:
        ws = Path(tempfile.mkdtemp(prefix="cline_test_"))

    # Run all cases
    results: list[TestResult] = []
    for case in cases:
        r = run_one(
            case=case,
            api_mode=args.api,
            selfopt_url=args.selfopt,
            workspace=ws,
            cdp_port=args.cdp_port,
            timeout_override=args.timeout,
        )
        results.append(r)

    # Report
    report_path = write_report(results, out_dir)
    print(f"\n{'='*60}")
    print(f"  Results: {report_path}")
    passed  = sum(1 for r in results if r.status == "pass")
    failed  = sum(1 for r in results if r.status == "fail")
    print(f"  {passed}/{len(results)} passed, {failed} failed")
    print(f"{'='*60}\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
