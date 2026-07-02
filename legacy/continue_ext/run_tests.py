#!/usr/bin/env python3
"""
tests/tools/continue_ext/run_tests.py
──────────────────────────────────────
Entry point for automated Continue extension integration tests.
Same CLI interface as cline/run_tests.py.
"""

from __future__ import annotations

import argparse
import sys
import time
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.tools.continue_ext.test_cases import all_cases, load_case
from tests.tools.continue_ext.runner import configure, open_panel, inject_prompt
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
    result = TestResult(name=case["name"], tool="continue", api_mode=api_mode)
    result.start()

    timeout       = timeout_override or case.get("timeout", 120)
    prompts       = case.get("prompts", [])
    setup_files   = case.get("setup_files", {})
    expected_spec = case.get("expected_files", [])

    setup_workspace(workspace, setup_files)
    before = snapshot(workspace)
    print(f"\n{'='*60}")
    print(f"  TEST: {case['name']} (Continue)")
    print(f"  API:  {api_mode} → {selfopt_url}")
    print(f"  WS:   {workspace}")
    print(f"{'='*60}")

    # Continue hot-reloads config — write it before launching VS Code
    try:
        warnings = configure(api_mode, selfopt_url)
        for w in warnings:
            result.warn(w)
    except Exception as e:
        print(f"  [run] FAIL configure(): {e}")
        result.fail(f"configure() failed: {e}")
        result.finish()
        return result

    if cdp_alive(cdp_port):
        print(f"  [run] stale VS Code on :{cdp_port} — killing before launch")
        kill_vscode(cdp_port)

    try:
        launch_vscode(workspace, cdp_port)
    except RuntimeError as e:
        print(f"  [run] FAIL launch_vscode(): {e}")
        result.fail(str(e))
        result.finish()
        return result

    time.sleep(5)  # Continue takes a bit longer to initialise

    with sync_playwright() as pw:
        browser = None
        try:
            try:
                browser, ctx, page = cdp_connect(pw, cdp_port)
            except Exception as e:
                print(f"  [run] FAIL cdp_connect(): {e}")
                result.fail(f"CDP connect failed: {e}")
                return result

            if not check_extension_installed("continue.continue"):
                print("  [run] WARN: Continue extension not found in extensions dir — proceeding anyway")
                result.warn("Continue extension not detected in extensions directory")

            if not open_panel(page, ctx):
                result.warn("Could not confirm Continue panel opened; continuing anyway")
            time.sleep(2)

            is_multi = len(prompts) > 1
            for i, prompt_text in enumerate(prompts):
                print(f"  [run] injecting prompt {i+1}/{len(prompts)}: {prompt_text[:60]}...")
                ok, msg = inject_prompt(page, prompt_text)
                if not ok:
                    print(f"  [run] FAIL inject_prompt turn {i+1}: {msg}")
                    result.fail(f"inject_prompt failed (turn {i+1}): {msg}")
                    return result

                if is_multi and i < len(prompts) - 1:
                    turn_timeout = min(timeout // len(prompts), 90)
                    auto_approve_loop(
                        ctx, workspace, before, [],
                        timeout=turn_timeout, poll_interval=2.0, idle_done_timeout=10,
                    )
                    before = snapshot(workspace)

            passed, created, err = auto_approve_loop(
                ctx, workspace, before, expected_spec,
                timeout=timeout, poll_interval=2.0, idle_done_timeout=15,
            )

            if passed:
                result.pass_()
                print(f"  [run] PASS — files: {[f.name for f in created]}")
            else:
                result.fail(err or "Expected files not created")
                print(f"  [run] FAIL — {err}")

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

    kill_vscode(cdp_port)
    result.finish()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run automated Continue integration tests")
    parser.add_argument("--api",       default="ollama", choices=["ollama", "openai"])
    parser.add_argument("--selfopt",   default="http://localhost:8001")
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
        except RuntimeError as e:
            print(f"ERROR: {e}")
            sys.exit(1)

    cases = [load_case(n) for n in args.test] if args.test else all_cases()
    if not cases:
        print("No test cases found.")
        sys.exit(0)

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.output) if args.output else (
        Path(__file__).parents[1] / "results" / f"continue_{ts}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    ws = Path(args.workspace) if args.workspace else Path(tempfile.mkdtemp(prefix="continue_test_"))
    ws.mkdir(parents=True, exist_ok=True)

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

    report_path = write_report(results, out_dir)
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    print(f"\n  Results: {report_path}")
    print(f"  {passed}/{len(results)} passed, {failed} failed\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
